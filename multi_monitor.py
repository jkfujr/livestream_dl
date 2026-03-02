import subprocess
import sys
import time
import os
import signal
import threading
import tomllib  # Python 3.11+ 内置
from typing import Dict, List, Optional

try:
    from fastapi import FastAPI, HTTPException, Request, Form
    from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
    import uvicorn
    import tomli_w
except ImportError:
    print("[系统] 缺少必要依赖，请运行: pip install fastapi uvicorn tomli-w python-multipart")
    sys.exit(1)

# ================= 全局配置 =================
CONFIG_FILE = "config.toml"
PYTHON_EXECUTABLE = sys.executable
SCRIPT_PATH = "runner.py"

# 全局进程字典 {channel_id: subprocess.Popen}
processes: Dict[str, subprocess.Popen] = {}
# 全局应用
app = FastAPI()

# ================= 辅助函数 =================

def load_config() -> dict:
    if not os.path.exists(CONFIG_FILE):
        return {"channels": [], "common_args": {}, "log_dir": "logs", "output_template": ""}
    try:
        with open(CONFIG_FILE, "rb") as f:
            return tomllib.load(f)
    except Exception as e:
        print(f"[系统] 读取配置失败: {e}")
        return {"channels": [], "common_args": {}, "log_dir": "logs", "output_template": ""}

def save_config(config: dict):
    try:
        with open(CONFIG_FILE, "wb") as f:
            tomli_w.dump(config, f)
    except Exception as e:
        print(f"[系统] 保存配置失败: {e}")

def ensure_log_dir(log_dir):
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

def get_log_path(channel_info, config):
    log_dir = config.get("log_dir", "logs")
    c_id = channel_info["id"]
    channel_name = channel_info.get("name", c_id)
    safe_name = "".join([c for c in channel_name if c.isalnum() or c in (' ', '-', '_')]).strip()
    return os.path.join(log_dir, f"{safe_name}_{c_id}.log")

def parse_common_args(common_args_dict):
    args = []
    for key, value in common_args_dict.items():
        if isinstance(value, bool):
            if value:
                args.append(f"--{key}")
        else:
            args.append(f"--{key}")
            args.append(str(value))
    return args

def start_process(channel_info, config):
    c_id = channel_info["id"]
    
    # 如果已经在运行且未结束，则跳过
    if c_id in processes and processes[c_id].poll() is None:
        return

    output_template = config.get("output_template", "")
    common_args = parse_common_args(config.get("common_args", {}))
    log_file = get_log_path(channel_info, config)
    
    # 确保日志目录存在
    ensure_log_dir(os.path.dirname(log_file))
    
    channel_name = channel_info.get("name", c_id)
    final_output = output_template.replace("%(uploader)s", channel_name)
    
    cmd = [PYTHON_EXECUTABLE, SCRIPT_PATH, c_id] + common_args + ["--output", final_output, "--log-file", log_file]
    
    print(f"[系统] 启动监控进程: {channel_name} ({c_id})")
    
    p = subprocess.Popen(
        cmd,
        shell=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    processes[c_id] = p

def stop_process(c_id):
    if c_id in processes:
        p = processes[c_id]
        if p.poll() is None:
            print(f"[系统] 停止监控进程: {c_id}")
            p.terminate()
        del processes[c_id]

# ================= 后台监控线程 =================

def monitor_loop():
    while True:
        try:
            config = load_config()
            channels = config.get("channels", [])
            
            config_ids = set()

            for channel in channels:
                c_id = channel.get("id")
                if not c_id: continue
                
                config_ids.add(c_id)
                enabled = channel.get("enabled", False)
                
                is_running = c_id in processes and processes[c_id].poll() is None
                
                if enabled:
                    if not is_running:
                        start_process(channel, config)
                else:
                    if is_running:
                        stop_process(c_id)
            
            active_ids = list(processes.keys())
            for pid in active_ids:
                if pid not in config_ids:
                    stop_process(pid)
                    
        except Exception as e:
            print(f"[系统] 监控循环异常: {e}")
        
        time.sleep(2)

# ================= Web 界面 =================

@app.get("/", response_class=HTMLResponse)
async def read_root():
    config = load_config()
    channels = config.get("channels", [])
    
    rows = ""
    for c in channels:
        c_id = c.get("id")
        c_name = c.get("name", "")
        c_enabled = c.get("enabled", False)
        
        is_running = c_id in processes and processes[c_id].poll() is None
        status_text = "🟢 运行中" if is_running else "🔴 已停止"
        if c_enabled and not is_running:
            status_text = "🟡 启动中..."
        
        action_btn = ""
        if c_enabled:
            action_btn = f'<form action="/stop/{c_id}" method="post" style="display:inline"><button type="submit">停止</button></form>'
        else:
            action_btn = f'<form action="/start/{c_id}" method="post" style="display:inline"><button type="submit">启动</button></form>'
            
        log_btn = f'<a href="/logs/{c_id}" target="_blank"><button>日志</button></a>'
        delete_btn = f'<form action="/delete/{c_id}" method="post" style="display:inline" onsubmit="return confirm(\'确定删除吗？\');"><button type="submit">删除</button></form>'
        
        rows += f"""
        <tr>
            <td>{c_name}</td>
            <td>{c_id}</td>
            <td>{status_text}</td>
            <td>
                {action_btn}
                {log_btn}
                {delete_btn}
            </td>
        </tr>
        """
        
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>livestream_dl_webui</title>
        <meta charset="utf-8">
        <style>
            body {{ font-family: sans-serif; padding: 20px; }}
            table {{ border-collapse: collapse; width: 100%; }}
            th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
            th {{ background-color: #f2f2f2; }}
            button {{ padding: 5px 10px; margin-right: 5px; cursor: pointer; }}
            input {{ padding: 5px; margin-right: 10px; }}
        </style>
    </head>
    <body>
        <div style="border: 1px solid #ccc; padding: 15px; margin-bottom: 20px; background: #f9f9f9;">
            <h3>添加新频道</h3>
            <form action="/add" method="post">
                <label>频道名称 (用户名): <input type="text" name="name" required placeholder="例如: 禰󠄀月くろす"></label>
                <label>频道 ID: <input type="text" name="id" required placeholder="例如: UC..."></label>
                <button type="submit">添加</button>
            </form>
        </div>

        <table>
            <thead>
                <tr>
                    <th>名称</th>
                    <th>ID</th>
                    <th>状态</th>
                    <th>操作</th>
                </tr>
            </thead>
            <tbody>
                {rows}
            </tbody>
        </table>
        
        <p><i>页面每 5 秒自动刷新一次</i></p>
        <script>
            setTimeout(function(){{
               location.reload();
            }}, 5000);
        </script>
    </body>
    </html>
    """
    return html_content

@app.get("/logs/{c_id}", response_class=HTMLResponse)
async def view_logs(c_id: str):
    config = load_config()
    channels = config.get("channels", [])
    
    target_channel = None
    for c in channels:
        if c["id"] == c_id:
            target_channel = c
            break
            
    if not target_channel:
        return HTMLResponse("<h1>未找到该频道</h1>", status_code=404)
        
    log_path = get_log_path(target_channel, config)
    content = ""
    
    if os.path.exists(log_path):
        try:
            # 读取最后 200 行，避免文件过大
            with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
                content = "".join(lines[-200:]) if len(lines) > 200 else "".join(lines)
        except Exception as e:
            content = f"读取日志失败: {e}"
    else:
        content = "暂无日志文件 (可能尚未启动或正在初始化)"
        
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>日志 - {target_channel.get('name')}</title>
        <meta charset="utf-8">
        <style>
            body {{ background: #1e1e1e; color: #d4d4d4; font-family: monospace; padding: 20px; }}
            pre {{ white-space: pre-wrap; word-wrap: break-word; }}
            .header {{ margin-bottom: 20px; border-bottom: 1px solid #444; padding-bottom: 10px; }}
            a {{ color: #569cd6; text-decoration: none; }}
        </style>
    </head>
    <body>
        <div class="header">
            <h2>{target_channel.get('name')} ({c_id}) - 实时日志 (最后 200 行)</h2>
            <a href="javascript:location.reload()">刷新</a> | <a href="/">返回首页</a>
        </div>
        <pre id="log-content">{content}</pre>
        <script>
            window.scrollTo(0, document.body.scrollHeight);
            // 每 3 秒自动刷新
            setTimeout(function(){{
               location.reload();
            }}, 3000);
        </script>
    </body>
    </html>
    """
    return html

@app.post("/add")
async def add_channel(name: str = Form(...), id: str = Form(...)):
    config = load_config()
    if "channels" not in config:
        config["channels"] = []
    
    for c in config["channels"]:
        if c["id"] == id:
            c["name"] = name
            save_config(config)
            return RedirectResponse(url="/", status_code=303)
            
    config["channels"].append({
        "id": id, 
        "name": name, 
        "enabled": False
    })
    save_config(config)
    return RedirectResponse(url="/", status_code=303)

@app.post("/delete/{c_id}")
async def delete_channel(c_id: str):
    config = load_config()
    if "channels" in config:
        config["channels"] = [c for c in config["channels"] if c["id"] != c_id]
        save_config(config)
    return RedirectResponse(url="/", status_code=303)

@app.post("/start/{c_id}")
async def start_channel(c_id: str):
    config = load_config()
    if "channels" in config:
        for c in config["channels"]:
            if c["id"] == c_id:
                c["enabled"] = True
                break
        save_config(config)
    return RedirectResponse(url="/", status_code=303)

@app.post("/stop/{c_id}")
async def stop_channel(c_id: str):
    config = load_config()
    if "channels" in config:
        for c in config["channels"]:
            if c["id"] == c_id:
                c["enabled"] = False
                break
        save_config(config)
    return RedirectResponse(url="/", status_code=303)

def stop_server(signum=None, frame=None):
    print("\n[系统] 正在关闭服务...")
    for c_id in list(processes.keys()):
        stop_process(c_id)
    sys.exit(0)

if __name__ == "__main__":
    signal.signal(signal.SIGINT, stop_server)
    signal.signal(signal.SIGTERM, stop_server)
    
    t = threading.Thread(target=monitor_loop, daemon=True)
    t.start()
    
    print("[系统] WebUI 服务已启动: http://0.0.0.0:8001")
    uvicorn.run(app, host="0.0.0.0", port=8001, log_level="warning")
