"""
LLM配置和初始化
"""

from langchain_ollama import ChatOllama
from langchain_core.messages import SystemMessage, HumanMessage
from .config import CONFIG
import json
import asyncio
from typing import Dict, Any, Optional, List
from datetime import datetime
import os


# LLM配置
OLLAMA_MODEL = CONFIG['llm']['model']
OLLAMA_BASE_URL = CONFIG['llm']['base_url']

# 检查是否启用调试模式（显示LLM原始响应）
DEBUG_SHOW_LLM_RESPONSE = CONFIG['llm'].get('debug_show_response', False)

# 性能优化：配置LLM参数（支持结构化输出）
llm_kwargs = {
    "model": OLLAMA_MODEL,
    "base_url": OLLAMA_BASE_URL,
    "temperature": 0,  # 降低随机性，提高稳定性
    "num_predict": 16384,  # 最大输出长度
    "num_ctx": 81920,  # 增加上下文窗口
    "num_gpu": -1,
    "num_thread": 4,
}

llm_kwargs["format"] = "json"

# 显示调试模式状态
if DEBUG_SHOW_LLM_RESPONSE:
    print("[LLM] 🐛 调试模式已启用（将显示LLM原始响应）")

llm = ChatOllama(**llm_kwargs)


class LLMResponseParser:
    """LLM响应解析器"""
    
    # 添加调用追踪，防止重复日志输出
    _call_tracking = {}
    _lock = asyncio.Lock()
    
    @staticmethod
    async def parse_json_with_retry(
        conversation: List,
        expected_schema: Dict[str, Any],
        max_retries: int = 3,
        parser_name: str = "unknown",
        timeout: int = 3600,
        custom_validator: Optional[callable] = None
    ) -> Optional[Dict[str, Any]]:
        """
        使用重试机制解析JSON响应，让LLM自我修正
        
        Args:
            conversation: 对话历史
            expected_schema: 期望的JSON schema
            max_retries: 最大重试次数
            parser_name: 解析器名称（用于日志）
            timeout: 单次请求超时时间（秒）
            custom_validator: 自定义验证函数，接收dict返回bool
            
        Returns:
            解析后的JSON对象，失败返回None
        """
        
        # 生成唯一的调用ID来追踪
        import hashlib
        call_id = hashlib.md5(f"{parser_name}_{id(conversation)}_{datetime.now().timestamp()}".encode()).hexdigest()[:8]
        
        for attempt in range(max_retries):
            try:
                # 使用锁防止并发打印
                async with LLMResponseParser._lock:
                    # 检查是否最近已经打印过（1秒内）
                    now = datetime.now().timestamp()
                    last_print_key = f"{parser_name}_{attempt}"
                    last_print_time = LLMResponseParser._call_tracking.get(last_print_key, 0)
                    
                    # 只有距离上次打印超过1秒才打印
                    if now - last_print_time > 1.0:
                        print(f"[{parser_name}] 🔄 尝试 {attempt + 1}/{max_retries}，请求LLM中... (ID:{call_id})")
                        LLMResponseParser._call_tracking[last_print_key] = now
                        
                        # 清理过期的追踪记录（超过10秒）
                        LLMResponseParser._call_tracking = {
                            k: v for k, v in LLMResponseParser._call_tracking.items()
                            if now - v < 10
                        }
                
                # 调用LLM（使用JSON格式），添加超时处理
                response = await asyncio.wait_for(
                    asyncio.to_thread(llm.invoke, conversation),
                    timeout=timeout
                )
                response_text = response.content
                
                # 提取token使用信息
                token_info = ""
                if hasattr(response, 'response_metadata'):
                    metadata = response.response_metadata
                    if 'eval_count' in metadata or 'prompt_eval_count' in metadata:
                        prompt_tokens = metadata.get('prompt_eval_count', 0)
                        completion_tokens = metadata.get('eval_count', 0)
                        total_tokens = prompt_tokens + completion_tokens
                        token_info = f" [Token: 输入={prompt_tokens}, 输出={completion_tokens}, 总计={total_tokens}]"
                
                print(f"[{parser_name}] ✅ 收到响应，长度: {len(response_text)}{token_info}")
                
                # 调试模式：显示LLM原始响应和详细token信息
                if DEBUG_SHOW_LLM_RESPONSE:
                    print(f"\n{'='*60}")
                    print(f"[{parser_name}] 🐛 LLM原始响应:")
                    print(f"{'='*60}")
                    
                    # 显示详细的token使用信息
                    if hasattr(response, 'response_metadata'):
                        metadata = response.response_metadata
                        print(f"📊 Token统计:")
                        print(f"  - 输入tokens (prompt_eval_count): {metadata.get('prompt_eval_count', 'N/A')}")
                        print(f"  - 输出tokens (eval_count): {metadata.get('eval_count', 'N/A')}")
                        if 'prompt_eval_count' in metadata and 'eval_count' in metadata:
                            total = metadata.get('prompt_eval_count', 0) + metadata.get('eval_count', 0)
                            print(f"  - 总计tokens: {total}")
                        if 'eval_duration' in metadata:
                            # 转换纳秒到秒
                            duration_s = metadata['eval_duration'] / 1e9
                            print(f"  - 生成耗时: {duration_s:.2f}秒")
                            if 'eval_count' in metadata and metadata['eval_count'] > 0:
                                tokens_per_sec = metadata['eval_count'] / duration_s
                                print(f"  - 生成速度: {tokens_per_sec:.2f} tokens/秒")
                        print(f"{'='*60}")
                    
                    # 如果响应很长，只显示前1000字符
                    if len(response_text) > 1000:
                        print(response_text[:1000])
                        print(f"\n... (还有 {len(response_text) - 1000} 个字符)")
                    else:
                        print(response_text)
                    print(f"{'='*60}\n")
                
                # 尝试解析JSON
                try:
                    result = json.loads(response_text)
                    print(f"[{parser_name}] 📝 JSON解析成功，验证schema中...")
                    
                    # 验证schema - 优先使用自定义验证器
                    if custom_validator:
                        if custom_validator(result):
                            print(f"[{parser_name}] ✅ 自定义验证通过，解析完成！")
                            return result
                        else:
                            print(f"[{parser_name}] ⚠️ 自定义验证失败")
                            print(f"[{parser_name}] 实际字段: {list(result.keys())}")
                            error_msg = "JSON结构不符合自定义验证规则"
                    elif LLMResponseParser._validate_schema(result, expected_schema):
                        print(f"[{parser_name}] ✅ Schema验证通过，解析完成！")
                        return result
                    else:
                        print(f"[{parser_name}] ⚠️ Schema验证失败")
                        print(f"[{parser_name}] 期望字段: {list(expected_schema.keys())}")
                        print(f"[{parser_name}] 实际字段: {list(result.keys())}")
                        error_msg = "JSON结构不符合预期schema"
                        
                except json.JSONDecodeError as e:
                    print(f"[{parser_name}] ⚠️ JSON解析失败: {str(e)}")
                    print(f"[{parser_name}] 响应前200字符: {response_text[:200]}...")
                    error_msg = f"JSON格式错误: {str(e)}"
                
                # 记录失败
                LLMResponseParser._log_parse_failure(
                    parser_name=parser_name,
                    attempt=attempt + 1,
                    response_text=response_text,
                    error_msg=error_msg
                )
                
                # 如果还有重试机会，让LLM自我修正
                if attempt < max_retries - 1:
                    print(f"[{parser_name}] 🔄 准备重试 {attempt + 2}/{max_retries}，请求LLM自我修正...")
                    
                    conversation.append(HumanMessage(content=response_text))
                    conversation.append(HumanMessage(content=f"""
上一次的响应解析失败：{error_msg}

请严格按照以下要求重新生成：
1. 只输出纯JSON，不要添加任何解释文字
2. 不要使用markdown代码块标记（如 ```json）
3. 确保JSON格式完全正确（双引号、逗号、括号匹配）
4. 必须包含以下字段：{list(expected_schema.keys())}

请重新输出：
"""))
                    
            except asyncio.TimeoutError:
                print(f"[{parser_name}] ⏱️ 请求超时（{timeout}秒）")
                LLMResponseParser._log_parse_failure(
                    parser_name=parser_name,
                    attempt=attempt + 1,
                    response_text="",
                    error_msg=f"Timeout after {timeout} seconds"
                )
                
                if attempt < max_retries - 1:
                    print(f"[{parser_name}] 将在下次尝试中使用更长的超时时间...")
                    timeout = int(timeout * 1.5)  # 增加超时时间
                    
            except Exception as e:
                print(f"[{parser_name}] ❌ 异常: {str(e)}")
                import traceback
                print(f"[{parser_name}] 堆栈追踪:\n{traceback.format_exc()}")
                LLMResponseParser._log_parse_failure(
                    parser_name=parser_name,
                    attempt=attempt + 1,
                    response_text="",
                    error_msg=f"Exception: {str(e)}\n{traceback.format_exc()}"
                )
                
                if attempt < max_retries - 1:
                    conversation.append(HumanMessage(content=f"发生错误: {str(e)}，请重试"))
        
        # 所有重试都失败
        print(f"[{parser_name}] ❌ 所有重试均失败")
        return None
    
    @staticmethod
    def _validate_schema(data: Dict[str, Any], schema: Dict[str, Any]) -> bool:
        """验证JSON是否符合schema"""
        # 简单验证：检查必需字段是否存在
        for key in schema.keys():
            if key not in data:
                print(f"缺少字段: {key}")
                return False
        return True
    
    @staticmethod
    def _log_parse_failure(
        parser_name: str,
        attempt: int,
        response_text: str,
        error_msg: str
    ):
        """记录解析失败的详细信息"""
        try:
            # 确保logs目录存在
            log_dir = "logs/parse_failures"
            os.makedirs(log_dir, exist_ok=True)
            
            # 生成日志文件名
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_file = os.path.join(
                log_dir,
                f"{parser_name}_{timestamp}_attempt{attempt}.log"
            )
            
            # 写入日志
            with open(log_file, 'w', encoding='utf-8') as f:
                f.write(f"Parser: {parser_name}\n")
                f.write(f"Attempt: {attempt}\n")
                f.write(f"Timestamp: {timestamp}\n")
                f.write(f"Error: {error_msg}\n")
                f.write(f"\n{'='*60}\n")
                f.write(f"Response Text:\n")
                f.write(f"{'='*60}\n")
                f.write(response_text)
            
            print(f"[{parser_name}] 📝 失败日志已保存: {log_file}")
            
        except Exception as e:
            print(f"[{parser_name}] ⚠️ 无法保存日志: {str(e)}")


# 导出解析器
parser = LLMResponseParser()
