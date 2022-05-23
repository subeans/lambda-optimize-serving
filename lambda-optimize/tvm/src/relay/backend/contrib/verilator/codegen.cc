/*
 * Licensed to the Apache Software Foundation (ASF) under one
 * or more contributor license agreements.  See the NOTICE file
 * distributed with this work for additional information
 * regarding copyright ownership.  The ASF licenses this file
 * to you under the Apache License, Version 2.0 (the
 * "License"); you may not use this file except in compliance
 * with the License.  You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing,
 * software distributed under the License is distributed on an
 * "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
 * KIND, either express or implied.  See the License for the
 * specific language governing permissions and limitations
 * under the License.
 */

/*!
 * \file src/relay/backend/contrib/verilator/codegen.cc
 * \brief Implementation of Verilator codegen APIs.
 */

#include <tvm/relay/attrs/nn.h>
#include <tvm/relay/expr_functor.h>
#include <tvm/relay/transform.h>
#include <tvm/relay/type.h>
#include <tvm/runtime/module.h>
#include <tvm/runtime/registry.h>

#include <fstream>
#include <numeric>
#include <sstream>

#include "../../../../runtime/contrib/json/json_node.h"
#include "../../../../runtime/contrib/verilator/verilator_runtime.h"
#include "../../utils.h"
#include "../codegen_json/codegen_json.h"

namespace tvm {
namespace relay {
namespace contrib {

using namespace backend;

/*! \brief Verilator JSON serializer */
class VerilatorJSONSerializer : public backend::contrib::JSONSerializer {
  using JSONGraphNode = tvm::runtime::json::JSONGraphNode;
  using JSONGraphNodeEntry = tvm::runtime::json::JSONGraphNodeEntry;

 public:
  VerilatorJSONSerializer(const std::string& symbol, const Expr& expr)
      : JSONSerializer(symbol, expr) {}

  std::vector<JSONGraphNodeEntry> VisitExpr_(const CallNode* cn) override {
    Expr expr = GetRef<Expr>(cn);
    std::string name;
    const CallNode* call = cn;
    if (const auto* op_node = cn->op.as<OpNode>()) {
      name = op_node->name;
    } else {
      LOG(FATAL) << "Verilator JSON runtime does not support calls to " << cn->op->GetTypeKey();
    }

    std::vector<JSONGraphNodeEntry> inputs;
    for (const auto& arg : cn->args) {
      auto res = VisitExpr(arg);
      inputs.insert(inputs.end(), res.begin(), res.end());
    }
    auto node = std::make_shared<JSONGraphNode>(name,     /* name_ */
                                                "kernel", /* op_type_ */
                                                inputs, 1 /* num_outputs_ */);
    SetCallNodeAttribute(node, call);
    return AddNode(node, GetRef<Expr>(cn));
  }
};

/*! \brief Attributes to store options for Verilator */
struct VerilatorOptionsNode : public tvm::AttrsNode<VerilatorOptionsNode> {
  String lib_path;
  int reset_cycles;
  bool profiler_enable;
  int profiler_cycle_counter_id;

  TVM_DECLARE_ATTRS(VerilatorOptionsNode, "ext.attrs.VerilatorOptionsNode") {
    TVM_ATTR_FIELD(lib_path).describe("the design library path").set_default("libverilator.so");
    TVM_ATTR_FIELD(reset_cycles).describe("the number of reset cycles").set_default(1);
    TVM_ATTR_FIELD(profiler_enable).describe("enable profiler").set_default(false);
    TVM_ATTR_FIELD(profiler_cycle_counter_id).describe("profiler cycle counter id").set_default(0);
  }
};

class VerilatorOptions : public Attrs {
 public:
  TVM_DEFINE_NOTNULLABLE_OBJECT_REF_METHODS(VerilatorOptions, Attrs, VerilatorOptionsNode);
};

TVM_REGISTER_NODE_TYPE(VerilatorOptionsNode);
TVM_REGISTER_PASS_CONFIG_OPTION("relay.ext.verilator.options", VerilatorOptions);

/*!
 * \brief The Verilator codegen tool. It takes a Relay expression/module and
 * compile it into a Verilator runtime module.
 */
runtime::Module VerilatorBackend(const ObjectRef& ref) {
  CHECK(ref->IsInstance<FunctionNode>());
  auto func = Downcast<Function>(ref);
  auto func_name = GetExtSymbol(func);
  VerilatorJSONSerializer serializer(func_name, func);
  serializer.serialize();
  std::string graph_json = serializer.GetJSON();
  auto params = serializer.GetParams();

  // Create runtime object
  auto n = make_object<runtime::contrib::VerilatorRuntime>(func_name, graph_json, params);

  // Get Verilator compiler options
  auto ctx = transform::PassContext::Current();
  auto cfg = ctx->GetConfig<VerilatorOptions>("relay.ext.verilator.options");
  if (!cfg.defined()) {
    cfg = AttrsWithDefaultValues<VerilatorOptions>();
  }

  n->SetLibrary(cfg.value()->lib_path);
  n->SetResetCycles(cfg.value()->reset_cycles);

  if (cfg.value()->profiler_enable) {
    n->EnableProfiler();
    n->SetProfilerCycleCounterId(cfg.value()->profiler_cycle_counter_id);
  }

  return runtime::Module(n);
}

TVM_REGISTER_GLOBAL("relay.ext.verilator").set_body_typed(VerilatorBackend);

}  // namespace contrib
}  // namespace relay
}  // namespace tvm
