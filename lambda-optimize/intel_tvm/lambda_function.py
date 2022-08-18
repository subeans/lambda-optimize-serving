# image-classification & nlp converter 

import time
import numpy as np
import os
import boto3
import torch
import json

BUCKET_NAME = os.environ.get('BUCKET_NAME')
s3_client = boto3.client('s3') 

def check_results(prefix,model_size,model_name,batchsize):    
    exist=False

    obj_list = s3_client.list_objects(Bucket=BUCKET_NAME,Prefix=prefix)

    check = prefix + f'{model_name}_{model_size}_{batchsize}.tar'
    contents_list = obj_list['Contents']
    for content in contents_list:
        # print(content)
        if content['Key']== check : 
            exist=True
            break
    return exist 

def update_results(model_name,model_size,batchsize,lambda_memory,convert_time):
    info = {'convert_time' : convert_time}
    with open(f'/tmp/{model_name}_{model_size}_{batchsize}_{lambda_memory}_convert.json','w') as f:
        json.dump(info, f, ensure_ascii=False, indent=4)  
    
    s3_client.upload_file(f'/tmp/{model_name}_{model_size}_{batchsize}_{lambda_memory}_convert.json',BUCKET_NAME,f'results/tvm/intel/{model_name}_{model_size}_{batchsize}_{lambda_memory}_convert.json')
    print("upload done : convert time results")

def load_model(framework,model_name,model_size):    
    import onnx

    if "onnx" in framework :
        framework = "onnx"
        os.makedirs(os.path.dirname(f'/tmp/{framework}/{model_name}_{model_size}/'), exist_ok=True)
        PATH = f"/tmp/{framework}/{model_name}_{model_size}/"
        s3_client.download_file(BUCKET_NAME, f'models/{framework}/{model_name}_{model_size}.onnx', f'/tmp/{framework}/{model_name}_{model_size}/model.onnx')
        model = onnx.load(PATH+'model.onnx')
    else:
        framework="torch"
        os.makedirs(os.path.dirname(f'/tmp/{framework}/{model_name}_{model_size}/'), exist_ok=True)
        PATH = f"/tmp/{framework}/{model_name}_{model_size}/"
        s3_client.download_file(BUCKET_NAME, f'models/{framework}/{model_name}_{model_size}/model.pt', f'/tmp/{framework}/{model_name}_{model_size}/model.pt')
        model = torch.load(PATH+'model.pt')
   
    return model

def optimize_tvm(wtype,framework, model,model_name,batchsize,model_size,imgsize=224,seq_length=128, layout="NCHW"):
    import tvm
    from tvm import relay

    # ImageClf input 
    if wtype == "img":
        if model_name == "inception_v3":
            imgsize=299
        input_shape = (batchsize, 3, imgsize, imgsize)
        data_array = np.random.uniform(0, 255, size=input_shape).astype("float32")
        torch_data = torch.tensor(data_array)

    # NLP input 
    elif wtype == "nlp":
        inputs = np.random.randint(0, 2000, size=(seq_length))
        token_types = np.random.randint(0,2,size=(seq_length))

        tokens_tensor = torch.tensor(np.array([inputs]))
        segments_tensors = torch.tensor(np.array([token_types]))

    if "onnx" in framework:   
        framework="onnx"
        if wtype == "img":
            shape_dict = {"input0": data_array.shape}
        elif wtype == "nlp":
            shape_dict = {"input0": [batchsize,seq_length]}
        mod, params = relay.frontend.from_onnx(model, shape=shape_dict)
        
    elif "torch" in framework:
        framework="torch"
        model.eval()
        # torch imageclf 
        if wtype == "img":
            traced_model = torch.jit.trace(model, torch_data)
            input_info = [('input0', input_shape)]
        # torch nlp
        elif wtype == "nlp":
            traced_model = torch.jit.trace(model, tokens_tensor,segments_tensors)
            input_info = [('input0', [batchsize,seq_length])]
        mod, params = relay.frontend.from_pytorch(traced_model, input_infos=input_info, default_dtype="float32")

    if layout == "NHWC":
        desired_layouts = {"nn.conv2d": ["NHWC", "default"]}
        seq = tvm.transform.Sequential(
            [
                relay.transform.RemoveUnusedFunctions(),
                relay.transform.ConvertLayout(desired_layouts),
            ]
        )
        with tvm.transform.PassContext(opt_level=3):
            mod = seq(mod)
    else:
        assert layout == "NCHW"

    target = "llvm -mcpu=core-avx2"
    
    convert_start_time = time.time()
    with tvm.transform.PassContext(opt_level=3,required_pass=["FastMath"]):
        mod = relay.transform.InferType()(mod)
        lib = relay.build(mod, target=target, params=params)
    convert_time = time.time() - convert_start_time

    os.makedirs(os.path.dirname(f'/tmp/tvm/intel/{model_name}/'), exist_ok=True)    
    lib.export_library(f"/tmp/tvm/intel/{model_name}/{model_name}_{batchsize}.tar")
    print("export done :",f"{model_name}_{batchsize}.tar")

    if framework=="onnx":
        s3_client.upload_file(f'/tmp/tvm/intel/{model_name}/{model_name}_{batchsize}.tar',BUCKET_NAME,f'models/tvm/intel/onnx/{model_name}_{model_size}_{batchsize}.tar')

    else:
        s3_client.upload_file(f'/tmp/tvm/intel/{model_name}/{model_name}_{batchsize}.tar',BUCKET_NAME,f'models/tvm/intel/{model_name}_{model_size}_{batchsize}.tar')
    
    print("S3 upload done")

    return convert_time

def lambda_handler(event, context):    
    workload_type = event['workload_type']
    model_name = event['model_name']
    model_size = event['model_size']
    framework = event['framework']
    configuration = event['configuration']
    batchsize = event['batchsize']
    user_email = event ['user_email']
    lambda_memory = event['lambda_memory']
    convert_time = 0

    # convert한 모델이 있는지 확인 
    if "onnx" in framework:
        prefix = 'models/tvm/intel/onnx/'
    else:
        prefix = 'models/tvm/intel/'
    exist = check_results(prefix,model_size,model_name,batchsize)

    if exist == False:
        if "tvm" in configuration["intel"] :
            start_time = time.time()
            model = load_model(framework,model_name,model_size)
            load_time = time.time() - start_time
            print("Model load time : ",load_time)

            print(f"Hardware optimize - {framework} model to TVM model")
            convert_time = optimize_tvm(workload_type,framework,model,model_name,batchsize,model_size)
            update_results(model_name,model_size,batchsize,lambda_memory,convert_time)

    return {
            'workload_type':workload_type,
            'model_name': model_name,
            'model_size': model_size,
            'framework': framework,
            'configuration': event['configuration'],
            'batchsize': batchsize,
            'user_email': user_email,
            'lambda_memory': lambda_memory
        }
