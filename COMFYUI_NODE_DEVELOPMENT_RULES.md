# ComfyUI 节点开发与注册规则

## 概述

本文档总结了开发 ComfyUI 节点时需要遵循的规则和最佳实践，确保节点能够正确注册和运行。

## 目录

- [1. 节点注册方式](#1-节点注册方式)
  - [1.1 v2 版本注册方式](#11-v2-版本注册方式)
  - [1.2 v3 版本注册方式](#12-v3-版本注册方式)
- [2. 节点基本结构](#2-节点基本结构)
  - [2.1 必需方法](#21-必需方法)
  - [2.2 可选方法](#22-可选方法)
- [3. 输入和输出定义](#3-输入和输出定义)
- [4. 最佳实践](#4-最佳实践)
- [5. 常见问题](#5-常见问题)
- [6. 示例](#6-示例)

## 1. 节点注册方式

ComfyUI 支持两种节点注册方式：v2 版本和 v3 版本。

### 1.1 v2 版本注册方式

适用于较旧版本的 ComfyUI，通过模块级别的 `NODE_CLASS_MAPPINGS` 和 `NODE_DISPLAY_NAME_MAPPINGS` 字典注册节点。

```python
# 节点类定义
class MyNode:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "input1": ("STRING", {"default": ""}),
            },
        }

    RETURN_TYPES = ("STRING",)
    FUNCTION = "process"

    def process(self, input1):
        return (input1,)

# 注册节点
NODE_CLASS_MAPPINGS = {
    "MyNode": MyNode,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MyNode": "My Node",
}
```

### 1.2 v3 版本注册方式

适用于较新版本的 ComfyUI，通过 `comfy_entrypoint` 函数注册节点。

```python
from comfy_api.latest import ComfyExtension, io as comfy_io
from typing_extensions import override

class MyNode(comfy_io.ComfyNode):
    @classmethod
    def define_schema(cls) -> comfy_io.Schema:
        return comfy_io.Schema(
            node_id="MyNode",
            display_name="My Node",
            category="My Category",
            inputs=[
                comfy_io.String.Input(
                    "input1",
                    default="",
                    tooltip="Input string",
                ),
            ],
            outputs=[
                comfy_io.String.Output(display_name="output1"),
            ],
        )

    @classmethod
    def execute(cls, input1):
        return (input1,)

class MyExtension(ComfyExtension):
    @override
    async def get_node_list(self):
        return [MyNode]

async def comfy_entrypoint():
    return MyExtension()
```

## 2. 节点基本结构

### 2.1 必需方法

#### INPUT_TYPES

定义节点的输入参数。返回一个字典，包含 "required" 和可选的 "optional" 键。

```python
@classmethod
def INPUT_TYPES(cls):
    return {
        "required": {
            "input1": ("STRING", {"default": ""}),
        },
        "optional": {
            "input2": ("INT", {"default": 0, "min": 0, "max": 100}),
        },
    }
```

#### RETURN_TYPES

定义节点的返回类型。返回一个元组，包含输出类型的字符串。

```python
RETURN_TYPES = ("STRING", "INT",)
```

#### RETURN_NAMES

（可选）定义返回值的显示名称。

```python
RETURN_NAMES = ("output1", "output2",)
```

#### FUNCTION

指定处理输入的方法名称。

```python
FUNCTION = "process"
```

#### execute (v3 版本)

在 v3 版本中，使用 `execute` 方法处理输入。

```python
@classmethod
def execute(cls, input1, input2=0):
    return (input1, input2,)
```

### 2.2 可选方法

#### IS_CHANGED

定义节点何时需要重新执行。

```python
@classmethod
def IS_CHANGED(cls, input1, input2):
    return (input1, input2,)
```

#### VALIDATE_INPUTS

验证输入参数。

```python
@classmethod
def VALIDATE_INPUTS(cls, input1, input2):
    if input2 < 0:
        return "input2 must be positive"
    return True
```

## 3. 输入和输出定义

### 输入类型

- `STRING`: 字符串输入
- `INT`: 整数输入
- `FLOAT`: 浮点数输入
- `BOOLEAN`: 布尔值输入
- `IMAGE`: 图像输入
- `LATENT`: 潜在表示输入
- `MODEL`: 模型输入
- `VAE`: VAE 模型输入
- `CLIP`: CLIP 模型输入
- 以及其他自定义类型

### 输入参数

输入参数可以包含以下属性：

- `default`: 默认值
- `min`: 最小值（适用于数值类型）
- `max`: 最大值（适用于数值类型）
- `step`: 步长（适用于数值类型）
- `multiline`: 是否多行（适用于字符串类型）
- `tooltip`: 提示信息

### 输出类型

- `STRING`: 字符串输出
- `INT`: 整数输出
- `FLOAT`: 浮点数输出
- `BOOLEAN`: 布尔值输出
- `IMAGE`: 图像输出
- `LATENT`: 潜在表示输出
- 以及其他自定义类型

## 4. 最佳实践

1. **导入位置**: 将 `comfy_api` 导入移到函数内部，以提高兼容性
2. **错误处理**: 添加适当的错误处理，确保节点在各种情况下都能正常运行
3. **文档**: 为节点和参数添加清晰的文档和提示
4. **分类**: 将节点放在合适的分类下，提高可发现性
5. **测试**: 测试节点在不同输入下的行为
6. **性能**: 优化节点的执行性能，特别是处理大型输入时

## 5. 常见问题

### 5.1 节点无法注册

- 检查 `NODE_CLASS_MAPPINGS` 是否正确定义（v2 版本）
- 检查 `comfy_entrypoint` 函数是否正确实现（v3 版本）
- 确保没有导入错误

### 5.2 节点无输出

- 检查 `RETURN_TYPES` 是否与 `execute` 方法的返回值匹配
- 确保 `execute` 方法返回正确类型的值

### 5.3 版本兼容性

- 使用条件导入处理不同版本的 ComfyUI
- 测试节点在不同版本的 ComfyUI 中是否正常工作

## 6. 示例

### 完整的 v3 版本节点示例

```python
import json
import os
from typing_extensions import override

NODE_DIR = os.path.dirname(os.path.abspath(__file__))

class WorkflowStringExtractor:
    @classmethod
    def define_schema(cls):
        from comfy_api.latest import io as comfy_io
        return comfy_io.Schema(
            node_id="WorkflowStringExtractor",
            display_name="📝 Workflow String Extractor",
            category="💡Lightx02/Civitai",
            inputs=[
                comfy_io.String.Input(
                    "workflow_json",
                    default="{}",
                    multiline=True,
                    tooltip="Workflow JSON string or path to workflow.json file",
                ),
                comfy_io.String.Input(
                    "node_type",
                    default="ShowText|pysssss",
                    tooltip="Node type to extract strings from",
                ),
            ],
            outputs=[
                comfy_io.String.Output(display_name="string_1"),
                comfy_io.String.Output(display_name="string_2"),
                comfy_io.String.Output(display_name="string_3"),
                comfy_io.String.Output(display_name="string_4"),
                comfy_io.String.Output(display_name="string_5"),
            ],
        )

    @classmethod
    def execute(cls, workflow_json, node_type):
        # 实现逻辑
        try:
            workflow_data = json.loads(workflow_json)
        except json.JSONDecodeError:
            if os.path.exists(workflow_json):
                try:
                    with open(workflow_json, 'r', encoding='utf-8') as f:
                        workflow_data = json.load(f)
                except Exception:
                    return ("", "", "", "", "")
            else:
                return ("", "", "", "", "")

        strings = []
        nodes = workflow_data.get("workflow", {}).get("nodes", [])
        for node in nodes:
            if node.get("type") == node_type:
                widgets_values = node.get("widgets_values", [])
                for value in widgets_values:
                    if isinstance(value, list):
                        for item in value:
                            if isinstance(item, str):
                                strings.append(item)
                    elif isinstance(value, str):
                        strings.append(value)

        while len(strings) < 5:
            strings.append("")

        return (strings[0], strings[1], strings[2], strings[3], strings[4])

async def comfy_entrypoint():
    from comfy_api.latest import ComfyExtension
    
    class MyExtension(ComfyExtension):
        @override
        async def get_node_list(self):
            return [WorkflowStringExtractor]
    
    return MyExtension()
```

## 结论

遵循这些规则和最佳实践，可以确保您的 ComfyUI 节点能够正确注册和运行，同时提高代码的可维护性和兼容性。