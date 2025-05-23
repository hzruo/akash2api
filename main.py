from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import StreamingResponse, HTMLResponse, RedirectResponse
from fastapi.background import BackgroundTasks
from contextlib import asynccontextmanager
import requests
from curl_cffi import requests as cffi_requests
import uuid
import json
import time
from typing import Optional
import asyncio
import base64
import tempfile
import os
import re
import threading
import logging
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor
import random

# 加载环境变量
load_dotenv(override=True)

# 配置日志
logging.basicConfig(
    level=logging.INFO,  # 改为 INFO 级别
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 修改全局数据存储
global_data = {
    "cookie": None,
    "cookies": None,
    "last_update": 0,
    "cookie_expires": 0,  # 添加 cookie 过期时间
    "is_refreshing": False  # 添加刷新状态标志
}

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时获取 cookie
    logger.info("Starting FastAPI application, initializing cookie fetcher...")
    
    # 创建并启动线程
    cookie_thread = threading.Thread(target=get_cookie_with_retry)
    cookie_thread.daemon = True  # 设置为守护线程
    cookie_thread.start()
    
    # 创建并启动自动刷新线程
    refresh_thread = threading.Thread(target=auto_refresh_cookie)
    refresh_thread.daemon = True
    refresh_thread.start()
    
    logger.info("Cookie fetcher and auto-refresh threads started")
    yield
    
    # 关闭时清理资源
    logger.info("Shutting down FastAPI application")
    global_data["cookie"] = None
    global_data["cookies"] = None
    global_data["last_update"] = 0
    global_data["is_refreshing"] = False

def get_cookie_with_retry(max_retries=3, retry_delay=5):
    """带重试机制的获取 cookie 函数"""
    retries = 0
    while retries < max_retries:
        logger.info(f"Cookie fetching attempt {retries + 1}/{max_retries}")
        cookie = get_cookie()
        if cookie:
            logger.info("Successfully retrieved cookie")
            return cookie
        
        retries += 1
        if retries < max_retries:
            logger.info(f"Retrying cookie fetch in {retry_delay} seconds...")
            time.sleep(retry_delay)
    
    logger.error(f"Failed to fetch cookie after {max_retries} attempts")
    return None

app = FastAPI(lifespan=lifespan)
security = HTTPBearer()

# OpenAI API Key 配置，可以通过环境变量覆盖
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", None)
logger.info(f"OPENAI_API_KEY is set: {OPENAI_API_KEY is not None}")
# logger.info(f"OPENAI_API_KEY value: {OPENAI_API_KEY}")

def get_random_browser_fingerprint():
    """生成随机的浏览器指纹"""
    # 随机选择浏览器版本
    chrome_versions = ["120", "121", "122", "123", "124", "125"]
    edge_versions = ["120", "121", "122", "123", "124", "125"]
    selected_version = random.choice(chrome_versions)
    edge_version = random.choice(edge_versions)
    
    # 随机选择操作系统
    os_versions = [
        "Windows NT 10.0; Win64; x64",
        "Macintosh; Intel Mac OS X 10_15_7",
        "Macintosh; Intel Mac OS X 11_0_1",
        "Macintosh; Intel Mac OS X 12_0_1"
    ]
    selected_os = random.choice(os_versions)
    
    # 随机选择语言偏好
    languages = [
        "en-US,en;q=0.9",
        "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
        "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
        "en-GB,en;q=0.9,en-US;q=0.8"
    ]
    selected_language = random.choice(languages)
    
    # 随机选择视口大小
    viewport_sizes = [
        (1920, 1080),
        (1366, 768),
        (1440, 900),
        (1536, 864),
        (1680, 1050)
    ]
    selected_viewport = random.choice(viewport_sizes)
    
    # 构建用户代理字符串
    user_agent = f"Mozilla/5.0 ({selected_os}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{selected_version}.0.0.0 Safari/537.36 Edg/{edge_version}.0.0.0"
    
    # 构建请求头
    headers = {
        "accept": "*/*",
        "accept-language": selected_language,
        "content-type": "application/json",
        "origin": "https://chat.akash.network",
        "referer": "https://chat.akash.network/",
        "sec-ch-ua": f'"Microsoft Edge";v="{edge_version}", "Not-A.Brand";v="8", "Chromium";v="{selected_version}"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"macOS"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "user-agent": user_agent
    }
    
    return {
        "headers": headers,
        "viewport": selected_viewport,
        "user_agent": user_agent
    }

def get_cookie():
    """获取 cookie 的函数"""
    browser = None
    context = None
    page = None
    
    try:
        logger.info("Starting cookie retrieval process...")
        
        # 获取随机浏览器指纹
        fingerprint = get_random_browser_fingerprint()
        logger.info(f"Using browser fingerprint: {fingerprint['user_agent']}")
        
        with sync_playwright() as p:
            try:
                # 启动浏览器
                logger.info("Launching browser...")
                browser = p.chromium.launch(
                    headless=True,
                    args=[
                        '--no-sandbox',
                        '--disable-dev-shm-usage',
                        '--disable-gpu',
                        '--disable-software-rasterizer',
                        '--disable-extensions',
                        '--disable-setuid-sandbox',
                        '--no-first-run',
                        '--no-zygote',
                        '--single-process',
                        f'--window-size={fingerprint["viewport"][0]},{fingerprint["viewport"][1]}',
                        '--disable-blink-features=AutomationControlled',
                        '--disable-features=IsolateOrigins,site-per-process'
                    ]
                )
                
                logger.info("Browser launched successfully")
                
                # 创建上下文，使用随机指纹
                logger.info("Creating browser context...")
                context = browser.new_context(
                    viewport={'width': fingerprint["viewport"][0], 'height': fingerprint["viewport"][1]},
                    user_agent=fingerprint["user_agent"],
                    locale='en-US',
                    timezone_id='America/New_York',
                    permissions=['geolocation'],
                    extra_http_headers=fingerprint["headers"]
                )
                
                # 添加脚本以覆盖 navigator.webdriver
                context.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => false,
                    });
                    // 更多指纹伪装
                    Object.defineProperty(navigator, 'plugins', {
                        get: () => [1, 2, 3, 4, 5],
                    });
                """)
                
                logger.info("Browser context created successfully")
                
                # 创建页面
                logger.info("Creating new page...")
                page = context.new_page()
                logger.info("Page created successfully")
                
                # 设置页面超时
                page.set_default_timeout(60000)
                
                # 访问目标网站，添加重试机制
                max_retries = 3
                retry_delay = 5
                
                for attempt in range(max_retries):
                    try:
                        logger.info(f"Navigating to target website (attempt {attempt + 1}/{max_retries})...")
                        page.goto("https://chat.akash.network/", timeout=50000)
                        break
                    except Exception as e:
                        if attempt == max_retries - 1:
                            raise
                        logger.warning(f"Navigation attempt {attempt + 1} failed: {e}")
                        time.sleep(retry_delay)
                
                # 等待页面加载
                logger.info("Waiting for page load...")
                try:
                    # 首先等待 DOM 加载完成
                    page.wait_for_load_state("domcontentloaded", timeout=30000)
                    logger.info("DOM content loaded")
                    
                    # 等待一段时间，让 Cloudflare 检查完成
                    logger.info("Waiting for Cloudflare check...")
                    time.sleep(5)
                    
                    # 尝试点击页面，模拟用户行为
                    try:
                        page.mouse.move(100, 100)
                        page.mouse.click(100, 100)
                        logger.info("Simulated user interaction")
                        
                        # 随机滚动页面
                        page.mouse.wheel(0, 100)
                        time.sleep(0.5)
                        page.mouse.wheel(0, -50)
                        logger.info("Simulated scrolling")
                    except Exception as e:
                        logger.warning(f"Failed to simulate user interaction: {e}")
                    
                    # 再次等待一段时间
                    time.sleep(5)
                    
                except Exception as e:
                    logger.warning(f"Timeout waiting for load state: {e}")
                
                # 等待更长时间确保页面完全加载
                try:
                    page.wait_for_load_state("networkidle", timeout=10000)
                    logger.info("Network idle reached")
                except Exception as e:
                    logger.warning(f"Timeout waiting for network idle: {e}")
                
                # 获取 cookies
                logger.info("Getting cookies...")
                cookies = context.cookies()
                
                if not cookies:
                    logger.error("No cookies found")
                    return None
                
                # 记录所有 cookie 名称以进行调试
                cookie_names = [cookie['name'] for cookie in cookies]
                logger.info(f"Retrieved cookies: {cookie_names}")
                    
                # 检查是否有 cf_clearance cookie
                cf_cookie = next((cookie for cookie in cookies if cookie['name'] == 'cf_clearance'), None)
                if not cf_cookie:
                    logger.error("cf_clearance cookie not found")
                    return None
                
                # 检查是否有 session_token cookie
                session_cookie = next((cookie for cookie in cookies if cookie['name'] == 'session_token'), None)
                if not session_cookie:
                    logger.error("session_token cookie not found")
                    # 继续执行，因为某些情况下可能不需要 session_token
                    
                # 构建 cookie 字符串
                cookie_str = '; '.join([f"{cookie['name']}={cookie['value']}" for cookie in cookies])
                logger.info(f"Cookie string length: {len(cookie_str)}")
                
                global_data["cookie"] = cookie_str
                global_data["cookies"] = cookies  # 保存完整的 cookies 列表
                global_data["last_update"] = time.time()
                
                # 设置 cookie 过期时间
                if session_cookie and 'expires' in session_cookie and session_cookie['expires'] > 0:
                    global_data["cookie_expires"] = session_cookie['expires']
                    logger.info(f"Session token expires at: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(session_cookie['expires']))}")
                else:
                    # 如果没有明确的过期时间，默认设置为30分钟后过期
                    global_data["cookie_expires"] = time.time() + 1800  # 30 分钟
                    logger.info("No explicit expiration in session_token cookie, setting default 30 minute expiration")
                
                logger.info("Successfully retrieved cookies")
                return cookie_str
                
            except Exception as e:
                logger.error(f"Error in browser operations: {e}")
                logger.error(f"Error type: {type(e)}")
                import traceback
                logger.error(f"Traceback: {traceback.format_exc()}")
                return None
            finally:
                # 确保资源被正确关闭
                try:
                    if page:
                        logger.info("Closing page...")
                        try:
                            page.close()
                            logger.info("Page closed successfully")
                        except Exception as e:
                            logger.error(f"Error closing page: {e}")
                except Exception as e:
                    logger.error(f"Error in page cleanup: {e}")
                
                try:
                    if context:
                        logger.info("Closing context...")
                        try:
                            context.close()
                            logger.info("Context closed successfully")
                        except Exception as e:
                            logger.error(f"Error closing context: {e}")
                except Exception as e:
                    logger.error(f"Error in context cleanup: {e}")
                
                try:
                    if browser:
                        logger.info("Closing browser...")
                        try:
                            browser.close()
                            logger.info("Browser closed successfully")
                        except Exception as e:
                            logger.error(f"Error closing browser: {e}")
                except Exception as e:
                    logger.error(f"Error in browser cleanup: {e}")
                
                # 确保所有资源都被清理
                page = None
                context = None
                browser = None
                
                # 主动触发垃圾回收
                import gc
                gc.collect()
                logger.info("Resource cleanup completed")
    
    except Exception as e:
        logger.error(f"Error fetching cookie: {str(e)}")
        logger.error(f"Error type: {type(e)}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
    
    # 最后再次确保资源被清理
    if page:
        try:
            page.close()
        except:
            pass
    if context:
        try:
            context.close()
        except:
            pass
    if browser:
        try:
            browser.close()
        except:
            pass
    
    # 主动触发垃圾回收
    import gc
    gc.collect()
    
    return None

# 添加刷新 cookie 的函数
async def refresh_cookie():
    """刷新 cookie 的函数，用于401错误触发"""
    logger.info("Refreshing cookie due to 401 error")
    
    # 如果已经在刷新中，等待一段时间
    if global_data["is_refreshing"]:
        logger.info("Cookie refresh already in progress, waiting...")
        # 等待最多10秒
        for _ in range(10):
            await asyncio.sleep(1)
            if not global_data["is_refreshing"]:
                break
    
    # 如果仍然在刷新中，强制刷新
    if global_data["is_refreshing"]:
        logger.info("Forcing cookie refresh due to 401 error")
        global_data["is_refreshing"] = False
    
    try:
        global_data["is_refreshing"] = True
        # 标记 cookie 为过期
        global_data["cookie_expires"] = 0
        # 调用同步函数进行cookie获取，使用线程池不阻塞事件循环
        executor = ThreadPoolExecutor(max_workers=1)
        loop = asyncio.get_event_loop()
        new_cookie = await loop.run_in_executor(executor, get_cookie_with_retry)
        return new_cookie
    finally:
        global_data["is_refreshing"] = False

async def background_refresh_cookie():
    """后台刷新 cookie 的函数，不影响接口调用"""
    if global_data["is_refreshing"]:
        logger.info("Cookie refresh already in progress, skipping")
        return
    
    try:
        global_data["is_refreshing"] = True
        logger.info("Starting background cookie refresh")
        
        # 使用线程池执行同步函数
        executor = ThreadPoolExecutor(max_workers=1)
        loop = asyncio.get_event_loop()
        new_cookie = await loop.run_in_executor(executor, get_cookie)
        
        if new_cookie:
            logger.info("Background cookie refresh successful")
            # 更新 cookie 和过期时间
            global_data["cookie"] = new_cookie
            global_data["last_update"] = time.time()
            # 查找 session_token cookie 的过期时间
            session_cookie = next((cookie for cookie in global_data["cookies"] if cookie['name'] == 'session_token'), None)
            if session_cookie and 'expires' in session_cookie and session_cookie['expires'] > 0:
                global_data["cookie_expires"] = session_cookie['expires']
                logger.info(f"Session token expires at: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(session_cookie['expires']))}")
            else:
                # 如果没有明确的过期时间，默认设置为30分钟后过期
                global_data["cookie_expires"] = time.time() + 1800
                logger.info("No explicit expiration in session_token cookie, setting default 30 minute expiration")
        else:
            logger.error("Background cookie refresh failed")
    except Exception as e:
        logger.error(f"Error in background cookie refresh: {e}")
    finally:
        global_data["is_refreshing"] = False

async def check_and_update_cookie():
    """检查并更新 cookie"""
    try:
        current_time = time.time()
        # 只在 cookie 不存在或已过期时刷新
        if not global_data["cookie"] or current_time >= global_data["cookie_expires"]:
            logger.info("Cookie expired or not available, starting refresh")
            try:
                # 使用线程池执行同步的 get_cookie 函数
                loop = asyncio.get_event_loop()
                with ThreadPoolExecutor() as executor:
                    new_cookie = await loop.run_in_executor(executor, get_cookie)
                
                if new_cookie:
                    logger.info("Cookie refresh successful")
                else:
                    logger.error("Cookie refresh failed")
            except Exception as e:
                logger.error(f"Error during cookie refresh: {e}")
                import traceback
                logger.error(f"Traceback: {traceback.format_exc()}")
        else:
            logger.info("Using existing cookie")
            
    except Exception as e:
        logger.error(f"Error in check_and_update_cookie: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")

async def get_api_key(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    # logger.info(f"Received token: {token}")
    
    # 如果设置了 OPENAI_API_KEY，则需要验证
    if OPENAI_API_KEY is not None:
        # 去掉 Bearer 前缀后再比较
        clean_token = token.replace("Bearer ", "") if token.startswith("Bearer ") else token
        # logger.info(f"Clean token: {clean_token}")
        if clean_token != OPENAI_API_KEY:
            logger.error(f"Token mismatch. Expected: {OPENAI_API_KEY}, Got: {clean_token}")
            raise HTTPException(
                status_code=401,
                detail="Invalid API key"
            )
        logger.info("API key validation passed")
    
    return True

async def validate_cookie(background_tasks: BackgroundTasks):
    # 检查并更新 cookie（如果需要）
    await check_and_update_cookie()
    
    # 等待 cookie 初始化完成
    max_wait = 30  # 最大等待时间（秒）
    start_time = time.time()
    while not global_data["cookie"] and time.time() - start_time < max_wait:
        await asyncio.sleep(1)
        logger.info("Waiting for cookie initialization...")
    
    # 检查是否有有效的 cookie
    if not global_data["cookie"]:
        logger.error("Cookie not available after waiting")
        raise HTTPException(
            status_code=503,
            detail="Service temporarily unavailable - Cookie not available"
        )
    
    logger.info("Cookie validation passed")
    return global_data["cookie"]

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

@app.get("/", response_class=HTMLResponse)
async def health_check():
    """健康检查端点，返回服务状态"""
    # 检查 cookie 状态
    cookie_status = "ok" if global_data["cookie"] else "error"
    status_color = "green" if cookie_status == "ok" else "red"
    status_text = "正常" if cookie_status == "ok" else "异常"
    
    # 获取当前时间（北京时间）
    current_time = datetime.now(timezone(timedelta(hours=8)))
    
    # 格式化 cookie 过期时间（北京时间）
    if global_data["cookie_expires"]:
        expires_time = datetime.fromtimestamp(global_data["cookie_expires"], timezone(timedelta(hours=8)))
        expires_str = expires_time.strftime("%Y-%m-%d %H:%M:%S")
        
        # 计算剩余时间
        time_left = global_data["cookie_expires"] - time.time()
        hours_left = int(time_left // 3600)
        minutes_left = int((time_left % 3600) // 60)
        
        if hours_left > 0:
            time_left_str = f"{hours_left}小时{minutes_left}分钟"
        else:
            time_left_str = f"{minutes_left}分钟"
    else:
        expires_str = "未知"
        time_left_str = "未知"
    
    # 格式化最后更新时间（北京时间）
    if global_data["last_update"]:
        last_update_time = datetime.fromtimestamp(global_data["last_update"], timezone(timedelta(hours=8)))
        last_update_str = last_update_time.strftime("%Y-%m-%d %H:%M:%S")
        
        # 计算多久前更新
        time_since_update = time.time() - global_data["last_update"]
        if time_since_update < 60:
            update_ago = f"{int(time_since_update)}秒前"
        elif time_since_update < 3600:
            update_ago = f"{int(time_since_update // 60)}分钟前"
        else:
            update_ago = f"{int(time_since_update // 3600)}小时前"
    else:
        last_update_str = "从未更新"
        update_ago = "未知"
    
    status = {
        "status": "ok",
        "cookie_status": {
            "status": cookie_status,
            "status_text": status_text,
            "status_color": status_color,
            "expires": expires_str,
            "time_left": time_left_str,
            "available": bool(global_data["cookie"]),
            "last_update": last_update_str,
            "update_ago": update_ago
        }
    }
    
    # 返回 HTML 响应
    return HTMLResponse(content=f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Akash API 服务状态</title>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <script>
            // 每30秒自动刷新页面
            setTimeout(function() {{
                location.reload();
            }}, 30000);
        </script>
        <style>
            body {{
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                margin: 0;
                padding: 20px;
                background-color: #f5f5f5;
                color: #333;
                line-height: 1.6;
            }}
            .container {{
                max-width: 800px;
                margin: 0 auto;
                background-color: white;
                padding: 30px;
                border-radius: 12px;
                box-shadow: 0 4px 12px rgba(0,0,0,0.1);
            }}
            .header {{
                display: flex;
                align-items: center;
                margin-bottom: 30px;
                border-bottom: 1px solid #eee;
                padding-bottom: 20px;
            }}
            .logo {{
                font-size: 24px;
                font-weight: bold;
                color: #2c3e50;
                margin-right: 15px;
                display: flex;
                align-items: center;
            }}
            .logo-icon {{
                margin-right: 10px;
                font-size: 28px;
            }}
            .status {{
                display: flex;
                align-items: center;
                margin-bottom: 30px;
            }}
            .status-dot {{
                width: 16px;
                height: 16px;
                border-radius: 50%;
                margin-right: 12px;
                box-shadow: 0 0 0 4px rgba(76, 175, 80, 0.2);
            }}
            .status-dot.green {{
                background-color: #4CAF50;
                box-shadow: 0 0 0 4px rgba(76, 175, 80, 0.2);
            }}
            .status-dot.red {{
                background-color: #f44336;
                box-shadow: 0 0 0 4px rgba(244, 67, 54, 0.2);
            }}
            .status-text {{
                font-size: 20px;
                font-weight: 600;
            }}
            .status-text.ok {{
                color: #4CAF50;
            }}
            .status-text.error {{
                color: #f44336;
            }}
            .info-section {{
                background-color: #f9f9f9;
                border-radius: 8px;
                padding: 20px;
                margin-top: 20px;
            }}
            .info-section h3 {{
                margin-top: 0;
                color: #2c3e50;
                font-size: 18px;
                border-bottom: 1px solid #eee;
                padding-bottom: 10px;
                display: flex;
                align-items: center;
            }}
            .info-section h3 i {{
                margin-right: 8px;
            }}
            .info-item {{
                margin: 15px 0;
                display: flex;
                justify-content: space-between;
                align-items: center;
            }}
            .label {{
                color: #666;
                font-weight: 500;
                display: flex;
                align-items: center;
            }}
            .label i {{
                margin-right: 8px;
                font-size: 16px;
            }}
            .value {{
                font-weight: 600;
                padding: 5px 10px;
                border-radius: 4px;
                background-color: #f0f0f0;
                display: flex;
                align-items: center;
                gap: 5px;
            }}
            .value .status-text {{
                font-weight: 600;
            }}
            .value .status-text.ok {{
                color: #4CAF50;
            }}
            .value .status-text.error {{
                color: #f44336;
            }}
            .value.available {{
                color: #4CAF50;
                background-color: rgba(76, 175, 80, 0.1);
            }}
            .value.unavailable {{
                color: #f44336;
                background-color: rgba(244, 67, 54, 0.1);
            }}
            .value i {{
                font-size: 16px;
            }}
            .footer {{
                margin-top: 30px;
                text-align: center;
                color: #999;
                font-size: 14px;
                border-top: 1px solid #eee;
                padding-top: 20px;
            }}
            .refresh-btn {{
                display: inline-block;
                background-color: #3498db;
                color: white;
                padding: 8px 16px;
                border-radius: 4px;
                text-decoration: none;
                margin-top: 20px;
                font-weight: 500;
                transition: background-color 0.3s;
            }}
            .refresh-btn:hover {{
                background-color: #2980b9;
            }}
            .action-buttons {{
                display: flex;
                justify-content: center;
                gap: 15px;
                margin-top: 20px;
            }}
            .action-btn {{
                display: inline-flex;
                align-items: center;
                background-color: #f8f9fa;
                color: #333;
                padding: 8px 16px;
                border-radius: 4px;
                text-decoration: none;
                font-weight: 500;
                transition: all 0.3s;
                border: 1px solid #ddd;
            }}
            .action-btn:hover {{
                background-color: #e9ecef;
                border-color: #ced4da;
            }}
            .action-btn i {{
                margin-right: 8px;
            }}
            .status-badge {{
                display: inline-block;
                padding: 4px 8px;
                border-radius: 4px;
                font-size: 14px;
                font-weight: 500;
                margin-left: 10px;
            }}
            .status-badge.ok {{
                background-color: rgba(76, 175, 80, 0.1);
                color: #4CAF50;
            }}
            .status-badge.error {{
                background-color: rgba(244, 67, 54, 0.1);
                color: #f44336;
            }}
            .time-info {{
                font-size: 14px;
                color: #666;
                margin-top: 5px;
            }}
            .api-info {{
                margin-top: 30px;
                background-color: #f0f7ff;
                border-radius: 8px;
                padding: 20px;
                border-left: 4px solid #3498db;
            }}
            .api-info h3 {{
                margin-top: 0;
                color: #2c3e50;
                font-size: 18px;
            }}
            .api-info p {{
                margin: 10px 0;
            }}
            .api-info code {{
                background-color: #e9ecef;
                padding: 2px 5px;
                border-radius: 3px;
                font-family: monospace;
            }}
            .contact-info {{
                display: flex;
                align-items: center;
                justify-content: center;
                gap: 15px;
                margin: 15px 0;
            }}
            .contact-avatar {{
                width: 40px;
                height: 40px;
                border-radius: 50%;
                object-fit: cover;
                border: 2px solid #eee;
            }}
            .contact-logo {{
                display: flex;
                align-items: center;
                justify-content: center;
            }}
            .contact-name {{
                font-weight: 600;
                color: #3498db;
                transition: color 0.3s;
                text-decoration: none;
            }}
            .contact-name:hover {{
                color: #2980b9;
                text-decoration: underline;
            }}
            .contact-email {{
                color: #666;
                font-size: 14px;
                text-decoration: none;
                transition: color 0.3s;
            }}
            .contact-email:hover {{
                color: #3498db;
                text-decoration: underline;
            }}
        </style>
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0-beta3/css/all.min.css">
    </head>
    <body>
        <div class="container">
            <div class="header">
                <div class="logo">
                    <i class="fas fa-robot"></i>
                    <span>Akash API</span>
                </div>
            </div>
            <div class="status">
                <div class="status-dot {status["cookie_status"]["status_color"]}"></div>
                <div class="status-text {status["cookie_status"]["status"]}">服务状态: {status["cookie_status"]["status_text"]}</div>
            </div>
            <div class="info-section">
            
                <h3><i class="fas fa-cookie"></i> Cookie 信息</h3>
                <div class="info-item">
                    <span class="label"><i class="fas fa-clock"></i> 过期时间:</span>
                    <span class="value">{status["cookie_status"]["expires"]}</span>
                </div>
                <div class="time-info">剩余时间: {status["cookie_status"]["time_left"]}</div>            
                <div class="info-item">
                    <span class="label"><i class="fas fa-history"></i> 最后更新:</span>
                    <span class="value">{status["cookie_status"]["last_update"]}</span>
                </div>
                <div class="time-info">更新时间: {status["cookie_status"]["update_ago"]}</div>
            </div>
            
            
            <div class="footer">
                <p>Akash API 服务 - 健康检查页面</p>
                <div class="contact-info">
                    <img src="https://gravatar.loli.net/avatar/91af699fa609b1b7730753f1ff96b835?s=50&d=retro" class="contact-avatar" alt="用户头像" />
                    <div>
                        <p>如遇服务异常，请及时联系：<a href="https://linux.do/u/hzruo" class="contact-name">云胡不喜</a></p>
                    </div>
                </div>
                <p>当前时间: {current_time.strftime("%Y-%m-%d %H:%M:%S")} (北京时间)</p>
            </div>
        </div>
    </body>
    </html>
    """)

@app.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    background_tasks: BackgroundTasks,
    api_key: bool = Depends(get_api_key),
    cookie: str = Depends(validate_cookie)
):
    try:
        data = await request.json()
        
        # 获取随机浏览器指纹
        fingerprint = get_random_browser_fingerprint()
        logger.info(f"Using browser fingerprint: {fingerprint['user_agent']}")
        
        chat_id = str(uuid.uuid4()).replace('-', '')[:16]
        
        # 确保系统消息正确处理
        system_message = data.get('system_message') or data.get('system', "You are a helpful assistant.")
        
        # 更新请求数据格式，与实际 Akash API 请求保持一致
        akash_data = {
            "id": chat_id,
            "messages": data.get('messages', []),
            "model": data.get('model', "DeepSeek-R1"),
            "system": system_message,
            "temperature": data.get('temperature', 0.6),
            "topP": data.get('top_p', 0.95),
            "context": []  # 添加 context 字段
        }
        
        # 记录当前使用的 cookie（部分隐藏）
        cookie_start = cookie[:20]
        cookie_end = cookie[-20:] if len(cookie) > 40 else ""
        logger.info(f"Using cookie: {cookie_start}...{cookie_end}")
        
        with requests.Session() as session:
            # 设置 Cookie 使用请求头方式
            session.headers.update(fingerprint["headers"])
            cookies_dict = {}
            
            # 解析 cookie 字符串到字典
            for cookie_item in cookie.split(';'):
                if '=' in cookie_item:
                    name, value = cookie_item.strip().split('=', 1)
                    cookies_dict[name] = value
            
            # 使用 cookies 参数而不是 headers['Cookie']
            response = session.post(
                'https://chat.akash.network/api/chat',
                json=akash_data,
                cookies=cookies_dict,
                stream=True
            )
            
            # 检查响应状态码，如果是 401 或 403，尝试刷新 cookie 并重试
            if response.status_code in [401, 403]:
                logger.info(f"Authentication failed with status {response.status_code}, refreshing cookie...")
                new_cookie = await refresh_cookie()
                if new_cookie:
                    logger.info("Successfully refreshed cookie, retrying request")
                    # 解析新 cookie 字符串到字典
                    new_cookies_dict = {}
                    for cookie_item in new_cookie.split(';'):
                        if '=' in cookie_item:
                            name, value = cookie_item.strip().split('=', 1)
                            new_cookies_dict[name] = value
                    
                    response = session.post(
                        'https://chat.akash.network/api/chat',
                        json=akash_data,
                        cookies=new_cookies_dict,
                        stream=True
                    )
            
            if response.status_code not in [200, 201]:
                logger.error(f"Akash API error: Status {response.status_code}, Response: {response.text}")
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"Akash API error: {response.text}"
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
                                    messages = await process_image_generation(msg_data, session, fingerprint["headers"], chat_id)
                                    if messages:
                                        return messages
                                    return None

                                # 创建新的事件循环
                                loop = asyncio.new_event_loop()
                                asyncio.set_event_loop(loop)
                                try:
                                    result_messages = loop.run_until_complete(process_and_send())
                                finally:
                                    loop.close()
                                
                                if result_messages:
                                    for message in result_messages:
                                        yield f"data: {json.dumps(message)}\n\n"
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
                                "model": data.get('model'),
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
        print(f"Error in chat_completions: {e}")
        import traceback
        print(traceback.format_exc())
        return {"error": str(e)}

@app.get("/v1/models")
async def list_models(
    background_tasks: BackgroundTasks,
    cookie: str = Depends(validate_cookie)
):
    try:
        # 获取随机浏览器指纹
        fingerprint = get_random_browser_fingerprint()
        logger.info(f"Using browser fingerprint: {fingerprint['user_agent']}")
        
        # 构建更符合实际请求的请求头
        headers = fingerprint["headers"]
        
        # 记录当前使用的 cookie（部分隐藏）
        cookie_start = cookie[:20]
        cookie_end = cookie[-20:] if len(cookie) > 40 else ""
        logger.info(f"Using cookie: {cookie_start}...{cookie_end}")
        logger.info("Sending request to get models...")
        
        with requests.Session() as session:
            # 设置会话的默认请求头
            session.headers.update(headers)
            
            # 解析 cookie 字符串到字典
            cookies_dict = {}
            for cookie_item in cookie.split(';'):
                if '=' in cookie_item:
                    name, value = cookie_item.strip().split('=', 1)
                    cookies_dict[name] = value
            
            response = session.get(
                'https://chat.akash.network/api/models',
                cookies=cookies_dict
            )
            
            logger.info(f"Models response status: {response.status_code}")
        
            # 检查响应状态码，如果是 401 或 403，尝试刷新 cookie 并重试
            if response.status_code in [401, 403]:
                logger.info(f"Authentication failed with status {response.status_code}, refreshing cookie...")
                new_cookie = await refresh_cookie()
                if new_cookie:
                    logger.info("Successfully refreshed cookie, retrying request")
                    
                    # 解析新 cookie 字符串到字典
                    new_cookies_dict = {}
                    for cookie_item in new_cookie.split(';'):
                        if '=' in cookie_item:
                            name, value = cookie_item.strip().split('=', 1)
                            new_cookies_dict[name] = value
                    
                    response = session.get(
                        'https://chat.akash.network/api/models',
                        cookies=new_cookies_dict
                    )
        
            if response.status_code not in [200, 201]:
                logger.error(f"Akash API error: Status {response.status_code}, Response: {response.text}")
                return {"error": f"Authentication failed. Status: {response.status_code}"}
        
            try:
                akash_response = response.json()
                logger.info(f"Received models data of type: {type(akash_response)}")
            except ValueError:
                logger.error(f"Invalid JSON response: {response.text[:100]}...")
                return {"error": "Invalid response format"}
            
            # 检查响应格式并适配
            models_list = []
            if isinstance(akash_response, list):
                # 如果直接是列表
                models_list = akash_response
            elif isinstance(akash_response, dict):
                # 如果是字典格式
                models_list = akash_response.get("models", [])
            else:
                logger.error(f"Unexpected response format: {type(akash_response)}")
                models_list = []
            
            # 转换为标准 OpenAI 格式
            openai_models = {
                "object": "list",
                "data": [
                    {
                        "id": model["id"] if isinstance(model, dict) else model,
                        "object": "model",
                        "created": int(time.time()),
                        "owned_by": "akash",
                        "permission": [{
                            "id": f"modelperm-{model['id'] if isinstance(model, dict) else model}",
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
                    } for model in models_list
                ]
            }
            
            return openai_models
            
    except Exception as e:
        logger.error(f"Error in list_models: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        return {"error": str(e)}

async def process_image_generation(msg_data: str, session: requests.Session, headers: dict, chat_id: str) -> Optional[list]:
    """处理图片生成的逻辑，返回多个消息块"""
    # 检查消息中是否包含jobId
    if "jobId='undefined'" in msg_data or "jobId=''" in msg_data:
        logger.error("Image generation failed: jobId is undefined or empty")
        return create_error_messages(chat_id, "Akash官网服务异常，无法生成图片,请稍后再试。")
        
    match = re.search(r"jobId='([^']+)' prompt='([^']+)' negative='([^']*)'", msg_data)
    if not match:
        logger.error(f"Failed to extract job_id from message: {msg_data[:100]}...")
        return create_error_messages(chat_id, "无法解析图片生成任务。请稍后再试。")
        
    job_id, prompt, negative = match.groups()
    
    # 检查job_id是否有效
    if not job_id or job_id == 'undefined' or job_id == 'null':
        logger.error(f"Invalid job_id: {job_id}")
        return create_error_messages(chat_id, "Akash服务异常，无法获取有效的任务ID。请稍后再试。")
    
    print(f"Starting image generation process for job_id: {job_id}")
    
    # 记录开始时间
    start_time = time.time()
    
    # 发送思考开始的消息
    think_msg = "<think>\n"
    think_msg += "🎨 Generating image...\n\n"
    think_msg += f"Prompt: {prompt}\n"
    
    try:
        # 检查图片状态和上传
        result = await check_image_status(session, job_id, headers)
        
        # 计算实际花费的时间
        elapsed_time = time.time() - start_time
        
        # 完成思考部分
        think_msg += f"\n🤔 Thinking for {elapsed_time:.1f}s...\n"
        think_msg += "</think>"
        
        # 返回两个独立的消息块
        messages = []
        
        # 第一个消息块：思考过程
        messages.append({
            "id": f"chatcmpl-{chat_id}-think",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": "AkashGen",
            "choices": [{
                "delta": {"content": think_msg},
                "index": 0,
                "finish_reason": None
            }]
        })
        
        # 第二个消息块：图片结果
        if result:
            image_msg = f"\n\n![Generated Image]({result})"
            messages.append({
                "id": f"chatcmpl-{chat_id}-image",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": "AkashGen",
                "choices": [{
                    "delta": {"content": image_msg},
                    "index": 0,
                    "finish_reason": None
                }]
            })
        else:
            fail_msg = "\n\n*Image generation or upload failed.*"
            messages.append({
                "id": f"chatcmpl-{chat_id}-fail",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": "AkashGen",
                "choices": [{
                    "delta": {"content": fail_msg},
                    "index": 0,
                    "finish_reason": None
                }]
            })
        
        return messages
    except Exception as e:
        logger.error(f"Error in image generation process: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        return create_error_messages(chat_id, "图片生成过程中发生错误。请稍后再试。")

def create_error_messages(chat_id: str, error_message: str) -> list:
    """创建错误消息块"""
    return [{
        "id": f"chatcmpl-{chat_id}-error",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": "AkashGen",
        "choices": [{
            "delta": {"content": f"\n\n**❌ {error_message}**"},
            "index": 0,
            "finish_reason": None
        }]
    }]

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

def auto_refresh_cookie():
    """自动刷新 cookie 的线程函数"""
    while True:
        try:
            current_time = time.time()
            # 只在 cookie 不存在或已过期时刷新
            if (not global_data["cookie"] or 
                current_time >= global_data["cookie_expires"]) and not global_data["is_refreshing"]:
                
                logger.info(f"Cookie status check: exists={bool(global_data['cookie'])}, expires_in={global_data['cookie_expires'] - current_time if global_data['cookie_expires'] > 0 else 'expired'}")
                logger.info("Cookie expired or not available, starting refresh")
                
                try:
                    global_data["is_refreshing"] = True
                    new_cookie = get_cookie()
                    if new_cookie:
                        logger.info("Cookie refresh successful")
                    else:
                        logger.error("Cookie refresh failed, will retry later")
                except Exception as e:
                    logger.error(f"Error during cookie refresh: {e}")
                    import traceback
                    logger.error(f"Traceback: {traceback.format_exc()}")
                finally:
                    global_data["is_refreshing"] = False
                    # 强制执行垃圾回收，释放内存
                    import gc
                    gc.collect()
            
            # 每60秒检查一次
            time.sleep(60)
        except Exception as e:
            logger.error(f"Error in auto-refresh thread: {e}")
            global_data["is_refreshing"] = False  # 确保出错时也重置标志
            # 强制执行垃圾回收，释放内存
            import gc
            gc.collect()
            time.sleep(60)  # 出错后等待60秒再继续

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=9000)