from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import StreamingResponse
from fastapi.background import BackgroundTasks
import requests
from curl_cffi import requests as cffi_requests  # 保留这个，用于获取cookies
import uuid
import json
import time
from typing import Optional
import asyncio
import base64
import tempfile
import os
import re

app = FastAPI()
security = HTTPBearer()

# OpenAI API Key 配置，可以通过环境变量覆盖
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", None)  # 设置为 None 表示不校验，或设置具体值,如"sk-proj-1234567890"

# 修改全局数据存储
global_data = {
    "cookie": None,
    "cookies": None,
    "last_update": 0
}

def get_cookie():
    try:
        # 使用 curl_cffi 发送请求
        response = cffi_requests.get(
            'https://chat.akash.network/',
            impersonate="chrome110",
            timeout=30
        )
        
        # 获取所有 cookies
        cookies = response.cookies.items()
        if cookies:
            cookie_str = '; '.join([f'{k}={v}' for k, v in cookies])
            global_data["cookie"] = cookie_str
            global_data["last_update"] = time.time()
            print(f"Got cookies: {cookie_str}")
            return cookie_str
                
    except Exception as e:
        print(f"Error fetching cookie: {e}")
    return None

async def check_and_update_cookie(background_tasks: BackgroundTasks):
    # 如果cookie超过30分钟，在后台更新
    if time.time() - global_data["last_update"] > 1800:
        background_tasks.add_task(get_cookie)

@app.on_event("startup")
async def startup_event():
    get_cookie()

async def get_api_key(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    
    # 如果设置了 OPENAI_API_KEY，则需要验证
    if OPENAI_API_KEY is not None:
        # 去掉 Bearer 前缀后再比较
        clean_token = token.replace("Bearer ", "") if token.startswith("Bearer ") else token
        if clean_token != OPENAI_API_KEY:
            raise HTTPException(
                status_code=401,
                detail="Invalid API key"
            )
    
    # 返回去掉 "Bearer " 前缀的token
    return token.replace("Bearer ", "") if token.startswith("Bearer ") else token

async def check_image_status(session: requests.Session, job_id: str, headers: dict) -> Optional[str]:
    """检查图片生成状态并获取生成的图片"""
    max_retries = 30
    for attempt in range(max_retries):
        try:
            print(f"\nAttempt {attempt + 1}/{max_retries} for job {job_id}")
            response = session.get(
                f'https://chat.akash.network/api/image-status?ids={job_id}',
                headers=headers
            )
            print(f"Status response code: {response.status_code}")
            status_data = response.json()
            
            if status_data and isinstance(status_data, list) and len(status_data) > 0:
                job_info = status_data[0]
                status = job_info.get('status')
                print(f"Job status: {status}")
                
                # 只有当状态为 completed 时才处理结果
                if status == "completed":
                    result = job_info.get("result")
                    if result and not result.startswith("Failed"):
                        print("Got valid result, attempting upload...")
                        image_url = await upload_to_xinyew(result, job_id)
                        if image_url:
                            print(f"Successfully uploaded image: {image_url}")
                            return image_url
                        print("Image upload failed")
                        return None
                    print("Invalid result received")
                    return None
                elif status == "failed":
                    print(f"Job {job_id} failed")
                    return None
                
                # 如果状态是其他（如 pending），继续等待
                await asyncio.sleep(1)
                continue
                    
        except Exception as e:
            print(f"Error checking status: {e}")
            return None
    
    print(f"Timeout waiting for job {job_id}")
    return None

@app.get("/")
async def health_check():
    """Health check endpoint"""
    return {"status": "ok"}

@app.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    api_key: str = Depends(get_api_key)
):
    try:
        data = await request.json()
        print(f"Chat request data: {data}")
        
        chat_id = str(uuid.uuid4()).replace('-', '')[:16]
        
        akash_data = {
            "id": chat_id,
            "messages": data.get('messages', []),
            "model": data.get('model', "DeepSeek-R1"),
            "system": data.get('system_message', "You are a helpful assistant."),
            "temperature": data.get('temperature', 0.6),
            "topP": data.get('top_p', 0.95)
        }
        
        headers = {
            "Content-Type": "application/json",
            "Cookie": f"session_token={api_key}",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
            "Origin": "https://chat.akash.network",
            "Referer": "https://chat.akash.network/",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "Connection": "keep-alive",
            "Priority": "u=1, i"
        }
        
        print(f"Sending request to Akash with headers: {headers}")
        print(f"Request data: {akash_data}")
        
        with requests.Session() as session:
            response = session.post(
                'https://chat.akash.network/api/chat',
                json=akash_data,
                headers=headers,
                stream=True
            )
            
            def generate():
                content_buffer = ""
                for line in response.iter_lines():
                    if not line:
                        continue
                        
                    try:
                        line_str = line.decode('utf-8')
                        msg_type, msg_data = line_str.split(':', 1)
                        
                        if msg_type == '0':
                            if msg_data.startswith('"') and msg_data.endswith('"'):
                                msg_data = msg_data.replace('\\"', '"')
                                msg_data = msg_data[1:-1]
                            msg_data = msg_data.replace("\\n", "\n")
                            
                            # 在处理消息时先判断模型类型
                            if data.get('model') == 'AkashGen' and "<image_generation>" in msg_data:
                                # 图片生成模型的特殊处理
                                async def process_and_send():
                                    end_msg = await process_image_generation(msg_data, session, headers, chat_id)
                                    if end_msg:
                                        chunk = {
                                            "id": f"chatcmpl-{chat_id}",
                                            "object": "chat.completion.chunk",
                                            "created": int(time.time()),
                                            "model": data.get('model'),
                                            "choices": [{
                                                "delta": {"content": end_msg},
                                                "index": 0,
                                                "finish_reason": None
                                            }]
                                        }
                                        return f"data: {json.dumps(chunk)}\n\n"
                                    return None

                                # 创建新的事件循环
                                loop = asyncio.new_event_loop()
                                asyncio.set_event_loop(loop)
                                try:
                                    result = loop.run_until_complete(process_and_send())
                                finally:
                                    loop.close()
                                
                                if result:
                                    yield result
                                continue
                            
                            content_buffer += msg_data
                            
                            chunk = {
                                "id": f"chatcmpl-{chat_id}",
                                "object": "chat.completion.chunk",
                                "created": int(time.time()),
                                "model": data.get('model'),
                                "choices": [{
                                    "delta": {"content": msg_data},
                                    "index": 0,
                                    "finish_reason": None
                                }]
                            }
                            yield f"data: {json.dumps(chunk)}\n\n"
                        
                        elif msg_type in ['e', 'd']:
                            chunk = {
                                "id": f"chatcmpl-{chat_id}",
                                "object": "chat.completion.chunk",
                                "created": int(time.time()),
                                "model": data.get('model'),  # 使用请求中指定的模型
                                "choices": [{
                                    "delta": {},
                                    "index": 0,
                                    "finish_reason": "stop"
                                }]
                            }
                            yield f"data: {json.dumps(chunk)}\n\n"
                            yield "data: [DONE]\n\n"
                            break
                            
                    except Exception as e:
                        print(f"Error processing line: {e}")
                        continue

            return StreamingResponse(
                generate(),
                media_type='text/event-stream',
                headers={
                    'Cache-Control': 'no-cache',
                    'Connection': 'keep-alive',
                    'Content-Type': 'text/event-stream'
                }
            )
    
    except Exception as e:
        return {"error": str(e)}

@app.get("/v1/models")
async def list_models(api_key: str = Depends(get_api_key)):
    try:
        headers = {
            "Content-Type": "application/json",
            "Cookie": f"session_token={api_key}",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
            "Origin": "https://chat.akash.network",
            "Referer": "https://chat.akash.network/",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "Connection": "keep-alive"
        }
        
        response = requests.get(
            'https://chat.akash.network/api/models',
            headers=headers
        )
        
        akash_response = response.json()
        
        # 转换为标准 OpenAI 格式
        openai_models = {
            "object": "list",
            "data": [
                {
                    "id": model["id"],
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "akash",
                    "permission": [{
                        "id": "modelperm-" + model["id"],
                        "object": "model_permission",
                        "created": int(time.time()),
                        "allow_create_engine": False,
                        "allow_sampling": True,
                        "allow_logprobs": True,
                        "allow_search_indices": False,
                        "allow_view": True,
                        "allow_fine_tuning": False,
                        "organization": "*",
                        "group": None,
                        "is_blocking": False
                    }]
                } for model in akash_response.get("models", [])
            ]
        }
        
        return openai_models
        
    except Exception as e:
        print(f"Error in list_models: {e}")
        return {"error": str(e)}

async def upload_to_xinyew(image_base64: str, job_id: str) -> Optional[str]:
    """上传图片到新野图床并返回URL"""
    try:
        print(f"\n=== Starting image upload for job {job_id} ===")
        print(f"Base64 data length: {len(image_base64)}")
        
        # 解码base64图片数据
        try:
            image_data = base64.b64decode(image_base64.split(',')[1] if ',' in image_base64 else image_base64)
            print(f"Decoded image data length: {len(image_data)} bytes")
        except Exception as e:
            print(f"Error decoding base64: {e}")
            print(f"First 100 chars of base64: {image_base64[:100]}...")
            return None
        
        # 创建临时文件
        with tempfile.NamedTemporaryFile(suffix='.jpeg', delete=False) as temp_file:
            temp_file.write(image_data)
            temp_file_path = temp_file.name
        
        try:
            filename = f"{job_id}.jpeg"
            print(f"Using filename: {filename}")
            
            # 准备文件上传
            files = {
                'file': (filename, open(temp_file_path, 'rb'), 'image/jpeg')
            }
            
            print("Sending request to xinyew.cn...")
            response = requests.post(
                'https://api.xinyew.cn/api/jdtc',
                files=files,
                timeout=30
            )
            
            print(f"Upload response status: {response.status_code}")
            if response.status_code == 200:
                result = response.json()
                print(f"Upload response: {result}")
                
                if result.get('errno') == 0:
                    url = result.get('data', {}).get('url')
                    if url:
                        print(f"Successfully got image URL: {url}")
                        return url
                    print("No URL in response data")
                else:
                    print(f"Upload failed: {result.get('message')}")
            else:
                print(f"Upload failed with status {response.status_code}")
                print(f"Response content: {response.text}")
            return None
                
        finally:
            # 清理临时文件
            try:
                os.unlink(temp_file_path)
            except Exception as e:
                print(f"Error removing temp file: {e}")
            
    except Exception as e:
        print(f"Error in upload_to_xinyew: {e}")
        import traceback
        print(traceback.format_exc())
        return None

async def process_image_generation(msg_data: str, session: requests.Session, headers: dict, chat_id: str) -> str:
    """处理图片生成的逻辑"""
    match = re.search(r"jobId='([^']+)' prompt='([^']+)' negative='([^']*)'", msg_data)
    if match:
        job_id, prompt, negative = match.groups()
        print(f"Starting image generation process for job_id: {job_id}")
        
        # 发送思考开始的消息
        start_time = time.time()
        end_msg = "<think>\n"
        end_msg += "🎨 Generating image...\n\n"
        end_msg += f"Prompt: {prompt}\n"
        
        # 检查图片状态和上传
        result = await check_image_status(session, job_id, headers)
        
        # 发送结束消息
        elapsed_time = time.time() - start_time
        end_msg += f"\n🤔 Thinking for {elapsed_time:.1f}s...\n"
        end_msg += "</think>\n\n"
        
        if result:  # result 现在是上传后的图片URL
            end_msg += f"![Generated Image]({result})"
        else:
            end_msg += "*Image generation or upload failed.*\n"
            
        return end_msg
    return ""

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=9000)