#!/bin/bash

export SF_URL="https://="

model="alexnet efficientnet_b0 inception_v3 mnasnet mobilenet_v2 resnet18 resnet50 shufflenet squeezenet vgg16"
model_size="233.1 20.5 104 17 13.6 44.7 97.8 8.9 4.8 527.8 548.1"
arr_model_size=($model_size)
lambda_memory="2048"

cnt=0
echo ${arr_model_size[$cnt]}
for m in $model; do
    for lm in $lambda_memory;do
        url_parmas='{"model_name":'$m',"framework":"torch","configuration":{"intel":["onnx","tvm","base"],"arm":["onnx","tvm","base"]},"lambda_memory":'$lm',"batchsize":1,"user_email":"subean@kookmin.ac.kr","model_size":'${arr_model_size[$cnt]}',"workload_type":"img"}'
        echo $url_parmas
        curl -X POST -H "Content-Type: application/json" -d ${url_parmas} $SF_URL
    ((cnt+=1))
    done
done
