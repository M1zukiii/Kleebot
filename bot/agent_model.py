"""Model/tool protocol only; Discord effects belong to the agent executor."""
import json


class ToolModel:
    def __init__(self, chat_model):
        self.chat = chat_model

    async def step(self, instructions, history, tools):
        client = self.chat._get_client()
        if self.chat.config.provider == 'deepseek':
            response = await self.chat.completion(
                model=self.chat.config.model,
                messages=[{'role': 'system', 'content': instructions}, *history],
                tools=[{'type': 'function', 'function': {key: value for key, value in tool.items() if key not in {'type', 'strict'}}} for tool in tools],
                parallel_tool_calls=False, max_tokens=1600,
                extra_body={'thinking': {'type': 'disabled'}},
            )
            choice = response.choices[0]
            if choice.finish_reason not in {'stop', 'tool_calls'}:
                raise ValueError('Incomplete agent response')
            history.append(choice.message.model_dump(exclude_none=True))
            calls = [{'id': call.id, 'name': call.function.name, 'arguments': call.function.arguments}
                     for call in choice.message.tool_calls or []]
            return calls, choice.message.content or ''
        response = await self.chat.response(
            model=self.chat.config.model, instructions=instructions, input=history,
            tools=tools, parallel_tool_calls=False, max_output_tokens=1600, store=False,
        )
        if response.status != 'completed':
            raise ValueError('Incomplete agent response')
        # Keep reasoning and function-call items for the next tool-result turn.
        history.extend(response.output)
        calls = [{'id': item.call_id, 'name': item.name, 'arguments': item.arguments}
                 for item in response.output if item.type == 'function_call']
        return calls, response.output_text or ''

    def result(self, history, call_id, result):
        output = json.dumps(result, ensure_ascii=False)
        if self.chat.config.provider == 'deepseek':
            history.append({'role': 'tool', 'tool_call_id': call_id, 'content': output})
        else:
            history.append({'type': 'function_call_output', 'call_id': call_id, 'output': output})
