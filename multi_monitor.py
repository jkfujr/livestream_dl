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
    from fastapi.responses import HTMLResponse, RedirectResponse
    import uvicorn
    import tomli_w
except ImportError:
    print("[系统] 缺少必要依赖，请运行: pip install fastapi uvicorn tomli-w")
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

    log_dir = config.get("log_dir", "logs")
    output_template = config.get("output_template", "")
    common_args = parse_common_args(config.get("common_args", {}))
    
    ensure_log_dir(log_dir)
    
    channel_name = channel_info.get("name", c_id)
    safe_name = "".join([c for c in channel_name if c.isalnum() or c in (' ', '-', '_')]).strip()
    log_file = os.path.join(log_dir, f"{safe_name}_{c_id}.log")
    
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
            # p.wait() # 不阻塞等待
        del processes[c_id]

# ================= 后台监控线程 =================

def monitor_loop():
    """
    后台循环：
    1. 检查 config.toml 中的 enabled 状态
    2. 如果 enabled=True 且进程未运行，则启动
    3. 如果 enabled=False 且进程在运行，则停止
    4. 如果 enabled=True 且进程意外退出，则重启
    """
    while True:
        try:
            config = load_config()
            channels = config.get("channels", [])
            
            # 创建当前配置中存在的 ID 集合
            config_ids = set()

            for channel in channels:
                c_id = channel.get("id")
                if not c_id: continue
                
                config_ids.add(c_id)
                enabled = channel.get("enabled", False)
                
                # 检查进程状态
                is_running = c_id in processes and processes[c_id].poll() is None
                
                if enabled:
                    if not is_running:
                        # 需要启动（包含意外退出的情况，这里会自动重启）
                        start_process(channel, config)
                else:
                    if is_running:
                        # 需要停止
                        stop_process(c_id)
            
            # 清理已经从配置中删除但还在运行的进程
            active_ids = list(processes.keys())
            for pid in active_ids:
                if pid not in config_ids:
                    stop_process(pid)
                    
        except Exception as e:
            print(f"[系统] 监控循环异常: {e}")
        
        time.sleep(2) # 每2秒检查一次

# ================= Web 界面 =================

@app.get("/", response_class=HTMLResponse)
async def read_root():
    config = load_config()
    channels = config.get("channels", [])
    
    # 生成 HTML 表格行
    rows = ""
    for c in channels:
        c_id = c.get("id")
        c_name = c.get("name", "")
        c_enabled = c.get("enabled", False)
        
        # 检查实际运行状态
        is_running = c_id in processes and processes[c_id].poll() is None
        status_text = "🟢 运行中" if is_running else "🔴 已停止"
        if c_enabled and not is_running:
            status_text = "🟡 启动中..."
        
        action_btn = ""
        if c_enabled:
            action_btn = f'<form action="/stop/{c_id}" method="post" style="display:inline"><button type="submit">停止</button></form>'
        else:
            action_btn = f'<form action="/start/{c_id}" method="post" style="display:inline"><button type="submit">启动</button></form>'
            
        delete_btn = f'<form action="/delete/{c_id}" method="post" style="display:inline" onsubmit="return confirm(\'确定删除吗？\');"><button type="submit">删除</button></form>'
        
        rows += f"""
        <tr>
            <td>{c_name}</td>
            <td>{c_id}</td>
            <td>{status_text}</td>
            <td>
                {action_btn}
                {delete_btn}
            </td>
        </tr>
        """
        
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>直播监控管理</title>
        <meta charset="utf-8">
    </head>
    <body>
        <h1>直播监控管理面板</h1>
        
        <div style="border: 1px solid #ccc; padding: 10px; margin-bottom: 20px;">
            <h3>添加新频道</h3>
            <form action="/add" method="post">
                <label>频道名称 (用户名): <input type="text" name="name" required></label>
                <label>频道 ID: <input type="text" name="id" required></label>
                <button type="submit">添加</button>
            </form>
        </div>

        <table border="1" cellpadding="10" cellspacing="0">
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
        
        <p><i>刷新页面以查看最新状态</i></p>
    </body>
    </html>
    """
    return html_content

@app.post("/add")
async def add_channel(name: str = Form(...), id: str = Form(...)):
    config = load_config()
    if "channels" not in config:
        config["channels"] = []
    
    # 检查是否已存在
    for c in config["channels"]:
        if c["id"] == id:
            # 更新名称
            c["name"] = name
            save_config(config)
            return RedirectResponse(url="/", status_code=303)
            
    # 添加新频道，默认 enabled = False
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

# ================= 启动入口 =================

def stop_server(signum=None, frame=None):
    print("\n[系统] 正在关闭服务...")
    for c_id in list(processes.keys()):
        stop_process(c_id)
    sys.exit(0)

if __name__ == "__main__":
    signal.signal(signal.SIGINT, stop_server)
    signal.signal(signal.SIGTERM, stop_server)
    
    # 启动后台监控线程
    t = threading.Thread(target=monitor_loop, daemon=True)
    t.start()
    
    print("[系统] WebUI 服务已启动: http://0.0.0.0:8001")
    uvicorn.run(app, host="0.0.0.0", port=8001, log_level="warning")
