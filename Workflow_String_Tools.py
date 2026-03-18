import json
import os

NODE_DIR = os.path.dirname(os.path.abspath(__file__))

# ----- SECTION: Core Logic -----  
class WorkflowStringExtractorLogic:
    @classmethod
    def execute(cls, workflow_json, node_type):
        # 尝试解析为JSON字符串
        try:
            workflow_data = json.loads(workflow_json)
        except json.JSONDecodeError:
            # 尝试作为文件路径读取
            if os.path.exists(workflow_json):
                try:
                    with open(workflow_json, 'r', encoding='utf-8') as f:
                        workflow_data = json.load(f)
                except Exception:
                    return ("", "", "", "", "")
            else:
                return ("", "", "", "", "")

        strings = []
        
        # 从workflow.nodes中提取指定类型的节点
        nodes = workflow_data.get("workflow", {}).get("nodes", [])
        for node in nodes:
            if node.get("type") == node_type:
                # 从widgets_values中提取字符串
                widgets_values = node.get("widgets_values", [])
                for value in widgets_values:
                    if isinstance(value, list):
                        # 处理列表类型的widgets_values
                        for item in value:
                            if isinstance(item, str):
                                strings.append(item)
                    elif isinstance(value, str):
                        strings.append(value)

        # 确保返回5个字符串
        while len(strings) < 5:
            strings.append("")
        
        return (strings[0], strings[1], strings[2], strings[3], strings[4])

class StringLengthEvaluatorLogic:
    @classmethod
    def execute(cls, string, min_length):
        actual_length = len(string)
        is_valid = actual_length >= min_length
        
        if is_valid:
            result = f"Valid: Length {actual_length} >= {min_length}"
        else:
            result = f"Invalid: Length {actual_length} < {min_length}"
        
        return (is_valid, actual_length, result)

class StringListLengthComparatorLogic:
    @classmethod
    def execute(cls, string_1, string_2):
        length_1 = len(string_1)
        length_2 = len(string_2)
        is_first_longer = length_1 > length_2
        
        if is_first_longer:
            result = f"First string is longer: {length_1} > {length_2}"
        elif length_1 == length_2:
            result = f"Strings have equal length: {length_1}"
        else:
            result = f"Second string is longer: {length_2} > {length_1}"
        
        return (is_first_longer, length_1, length_2, result)

# ----- SECTION: Entry Point (comfy_entrypoint) -----  
async def comfy_entrypoint():
    print("[Workflow_String_Tools] comfy_entrypoint called")
    try:
        from comfy_api.latest import ComfyExtension, io as comfy_io
        from typing_extensions import override
        print("[Workflow_String_Tools] Successfully imported comfy_api")
        
        # Define node classes dynamically
        class WorkflowStringExtractor(comfy_io.ComfyNode):
            @classmethod
            def define_schema(cls) -> comfy_io.Schema:
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
                            tooltip="Node type to extract strings from (e.g., 'ShowText|pysssss')",
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
                return WorkflowStringExtractorLogic.execute(workflow_json, node_type)

        class StringLengthEvaluator(comfy_io.ComfyNode):
            @classmethod
            def define_schema(cls) -> comfy_io.Schema:
                return comfy_io.Schema(
                    node_id="StringLengthEvaluator",
                    display_name="📏 String Length Evaluator",
                    category="💡Lightx02/Civitai",
                    inputs=[
                        comfy_io.String.Input(
                            "string",
                            default="",
                            multiline=True,
                            tooltip="String to evaluate",
                        ),
                        comfy_io.Int.Input(
                            "min_length",
                            default=100,
                            min=1,
                            max=10000,
                            step=1,
                            tooltip="Minimum required string length",
                        ),
                    ],
                    outputs=[
                        comfy_io.Boolean.Output(display_name="is_valid"),
                        comfy_io.Int.Output(display_name="actual_length"),
                        comfy_io.String.Output(display_name="evaluation_result"),
                    ],
                )

            @classmethod
            def execute(cls, string, min_length):
                return StringLengthEvaluatorLogic.execute(string, min_length)

        class StringListLengthComparator(comfy_io.ComfyNode):
            @classmethod
            def define_schema(cls) -> comfy_io.Schema:
                return comfy_io.Schema(
                    node_id="StringListLengthComparator",
                    display_name="⚖️ String Length Comparator",
                    category="💡Lightx02/Civitai",
                    inputs=[
                        comfy_io.String.Input(
                            "string_1",
                            default="",
                            multiline=True,
                            tooltip="First string to compare",
                        ),
                        comfy_io.String.Input(
                            "string_2",
                            default="",
                            multiline=True,
                            tooltip="Second string to compare",
                        ),
                    ],
                    outputs=[
                        comfy_io.Boolean.Output(display_name="is_first_longer"),
                        comfy_io.Int.Output(display_name="length_1"),
                        comfy_io.Int.Output(display_name="length_2"),
                        comfy_io.String.Output(display_name="comparison_result"),
                    ],
                )

            @classmethod
            def execute(cls, string_1, string_2):
                return StringListLengthComparatorLogic.execute(string_1, string_2)

        class WorkflowStringToolsExtension(ComfyExtension):
            @override
            async def get_node_list(self):
                print("[Workflow_String_Tools] get_node_list called")
                return [WorkflowStringExtractor, StringLengthEvaluator, StringListLengthComparator]

        print("[Workflow_String_Tools] Successfully created extension")
        return WorkflowStringToolsExtension()
    except Exception as e:
        print(f"[Workflow_String_Tools] Error in comfy_entrypoint: {e}")
        import traceback
        traceback.print_exc()
        raise
