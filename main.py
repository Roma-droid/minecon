import os
import threading
import requests
import subprocess
import time
import psutil
import shutil
from flask import Flask, render_template, request, jsonify, send_file
import logging
import sqlite3
from contextlib import contextmanager
import atexit
from datetime import datetime
import zipfile
import json
import uuid
import re
import jupytext
import minecraft_launcher_lib

app = Flask(__name__)
servers = {}  # Активные серверы в памяти

# Настройки
API_SECRET = os.getenv('API_SECRET', 'default_secret')
DATABASE = 'minecraft_servers.db'

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Официальные репозитории
REPOSITORIES = {
    "vanilla": {"name": "Mojang Official", "url": "https://launchermeta.mojang.com/mc/game/version_manifest.json", "type": "mojang"},
    "paper": {"name": "PaperMC Official", "url": "https://api.papermc.io/v2/projects/paper", "type": "papermc"},
    "purpur": {"name": "Purpur Official", "url": "https://api.purpurmc.org/v2/purpur", "type": "purpur"},
    "fabric": {"name": "Fabric Official", "url": "https://meta.fabricmc.net/v2/versions", "type": "fabric"},
    "velocity": {"name": "Velocity Official", "url": "https://api.papermc.io/v2/projects/velocity", "type": "velocity"},
    "mrpack": {"name": "Modrinth Modpack", "url": "https://api.modrinth.com/v2", "type": "modrinth"}
}

# Определяем базовую директорию приложения
if os.name == 'nt':  # Windows
    BASE_DIR = os.path.abspath(os.path.dirname(__file__)).replace('\\', '/')
else:  # Linux/Mac
    BASE_DIR = os.path.abspath(os.path.dirname(__file__))

def get_full_path(*path_parts):
    """Создает полный абсолютный путь с правильными разделителями для ОС"""
    full_path = os.path.join(BASE_DIR, *path_parts)
    return os.path.abspath(full_path)

class MinecraftServer:
    def __init__(self, port, version, server_type="vanilla", temporary=False):
        self.port = port
        self.version = version
        self.server_type = server_type
        self.process = None
        self.status = "stopped"
        self.memory = "2G"
        self.motd = "Minecraft Server"
        self.max_players = 20
        self.auto_start = False
        self.temporary = temporary
        self.mrpack_minecraft_version = None
        
        if not temporary:
            self._save_to_db()
    
    def _save_to_db(self):
        """Сохранение сервера в БД"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''INSERT OR REPLACE INTO servers 
                (port, version, server_type, status, memory, motd, max_players, auto_start)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)''', 
                (self.port, self.version, self.server_type, self.status, 
                 self.memory, self.motd, self.max_players, self.auto_start))
            conn.commit()
    
    def update_status(self, status):
        """Обновление статуса сервера в БД"""
        self.status = status
        if self.temporary:
            return
            
        with get_db_connection() as conn:
            cursor = conn.cursor()
            if status == "running":
                cursor.execute('''UPDATE servers SET status = ?, last_started = CURRENT_TIMESTAMP WHERE port = ?''', 
                              (status, self.port))
            else:
                cursor.execute('''UPDATE servers SET status = ? WHERE port = ?''', (status, self.port))
            conn.commit()
    
    def add_log(self, message, level="INFO"):
        """Добавление лога в БД"""
        if self.temporary:
            return
            
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_entry = f"[{timestamp}] {message}"
        
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''INSERT INTO server_logs (server_port, log_text, log_level)
                VALUES (?, ?, ?)''', (self.port, log_entry, level))
            conn.commit()
        
        logger.info(f"Server {self.port}: {message}")
    
    def get_available_versions(self):
        """Получение доступных версий из официальных репозиториев"""
        try:
            repo = REPOSITORIES.get(self.server_type)
            if not repo:
                return []
            
            if repo["type"] == "mojang":
                response = requests.get(repo["url"])
                data = response.json()
                versions = [v["id"] for v in data["versions"] if v["type"] == "release"]
                return versions[:20]  # Ограничиваем количество
                
            elif repo["type"] == "papermc":
                response = requests.get(f"{repo['url']}/versions")
                if response.status_code == 200:
                    return response.json().get("versions", [])[:20]
                return []
                
            elif repo["type"] == "purpur":
                response = requests.get(repo['url'])
                if response.status_code == 200:
                    return response.json().get("versions", [])[:20]
                return []
                
            elif repo["type"] == "fabric":
                response = requests.get(f"{repo['url']}/game")
                if response.status_code == 200:
                    return [v["version"] for v in response.json() if v["stable"]][:20]
                return []
                
            elif repo["type"] == "velocity":
                response = requests.get(f"{repo['url']}/versions")
                if response.status_code == 200:
                    return response.json().get("versions", [])[:20]
                return []
                
            elif repo["type"] == "modrinth":
                # Для Modrinth modpacks возвращаем специальное значение для загруженных файлов
                return ["uploaded:mrpack"]
                
            else:
                return []
                
        except Exception as e:
            self.add_log(f"Error getting versions: {str(e)}", "ERROR")
            return []

    def start(self):
        if self.status == "running":
            return False
            
        try:
            server_dir = get_full_path("servers", str(self.port))
            # Используем полный путь для создания директории
            os.makedirs(server_dir, exist_ok=True)
            
            self.add_log(f"Starting server in directory: {server_dir}")
            
            # Скачивание сервера
            jar_path = self.download_server()
            if not jar_path:
                self.add_log("Failed to download server jar", "ERROR")
                return False
                
            if not os.path.exists(jar_path):
                self.add_log(f"Server jar not found at path: {jar_path}", "ERROR")
                # Покажем содержимое директории для отладки
                if os.path.exists(server_dir):
                    files = os.listdir(server_dir)
                    self.add_log(f"Files in server directory: {files}")
                return False
            
            # Дополнительная проверка
            if not os.path.isfile(jar_path):
                self.add_log(f"JAR path is not a file: {jar_path}", "ERROR")
                return False
                
            file_size = os.path.getsize(jar_path)
            if file_size == 0:
                self.add_log(f"JAR file is empty: {jar_path}", "ERROR")
                return False
                
            self.add_log(f"Using JAR file: {jar_path} (size: {file_size} bytes)")
            
            # Создание скрипта запуска
            start_script = self.create_start_script(jar_path)
            self.add_log(f"Start command: {' '.join(start_script)}")
            
            # Запуск процесса
            self.process = subprocess.Popen(
                start_script,
                cwd=server_dir,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True
            )
            
            self.status = "starting"
            self.update_status("starting")
            self.add_log(f"Server starting on port {self.port}")
            
            # Запуск мониторинга вывода
            threading.Thread(target=self.monitor_output, daemon=True).start()
            
            return True
            
        except Exception as e:
            self.add_log(f"Error starting server: {str(e)}", "ERROR")
            self.status = "error"
            self.update_status("error")
            return False

    def download_server(self):
        """Скачивание серверного jar файла"""
        try:
            server_dir = get_full_path("servers", str(self.port))
            
            # Создаем директорию, если не существует
            os.makedirs(server_dir, exist_ok=True)
            
            repo = REPOSITORIES.get(self.server_type)
            if not repo:
                self.add_log(f"Unknown server type: {self.server_type}", "ERROR")
                return None
            
            # Обработка mrpack (Modrinth modpack)
            if repo["type"] == "modrinth":
                return self._download_mrpack(server_dir)
            
            # Базовое имя файла (без расширения)
            jar_name = f"{self.server_type}-{self.version}.jar"
            jar_path = os.path.join(server_dir, jar_name)
            
            if os.path.exists(jar_path):
                self.add_log(f"Using existing jar: {jar_name}")
                return jar_path
            
            self.add_log(f"Downloading {self.server_type} version {self.version}")
            
            download_url = None
            
            if repo["type"] == "mojang":
                # Vanilla
                manifest = requests.get(repo["url"]).json()
                version_info = next((v for v in manifest["versions"] if v["id"] == self.version), None)
                if not version_info:
                    raise ValueError("Version not found")
                    
                version_data = requests.get(version_info["url"]).json()
                download_url = version_data["downloads"]["server"]["url"]
                
            elif repo["type"] == "papermc":
                # Paper
                builds_url = f"{repo['url']}/versions/{self.version}"
                builds = requests.get(builds_url).json()
                latest_build = builds["builds"][-1]
                
                download_url = f"{builds_url}/builds/{latest_build}/downloads/paper-{self.version}-{latest_build}.jar"
                
            elif repo["type"] == "purpur":
                # Purpur
                builds_url = f"{repo['url']}/{self.version}"
                builds = requests.get(builds_url).json()
                latest_build = builds["builds"]["latest"]
                
                download_url = f"{builds_url}/{latest_build}/downloads/purpur-{self.version}-{latest_build}.jar"
                
            elif repo["type"] == "fabric":
                # Fabric
                loader_versions = requests.get(f"{repo['url']}/loader").json()
                latest_loader = loader_versions[0]["loader"]["version"]
                
                installer_versions = requests.get(f"{repo['url']}/installer").json()
                latest_installer = installer_versions[0]["version"]
                
                download_url = f"https://meta.fabricmc.net/v2/versions/loader/{self.version}/{latest_loader}/{latest_installer}/server/jar"
                jar_name = "fabric-server.jar"
                jar_path = os.path.join(server_dir, jar_name)
                
            elif repo["type"] == "velocity":
                # Velocity
                builds_url = f"{repo['url']}/versions/{self.version}"
                builds = requests.get(builds_url).json()
                latest_build = builds["builds"][-1]
                
                download_url = f"{builds_url}/builds/{latest_build}/downloads/velocity-{self.version}-{latest_build}.jar"
                
            if not download_url:
                raise ValueError("Could not find download URL")
            
            # Скачивание файла
            response = requests.get(download_url, stream=True)
            if response.status_code != 200:
                raise ValueError(f"Download failed with status {response.status_code}")
            
            with open(jar_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            
            self.add_log(f"Downloaded {jar_name}")
            return jar_path
            
        except Exception as e:
            self.add_log(f"Error downloading server: {str(e)}", "ERROR")
            return None

    def _download_mrpack(self, server_dir):
        """Скачивание/обработка mrpack (теперь использует библиотеку)"""
        try:
            # Поскольку версия - это "uploaded:filename.mrpack"
            if self.version.startswith("uploaded:"):
                filename = self.version.split(":", 1)[1]
                mrpack_path = get_full_path("uploads", filename)
                
                if not os.path.exists(mrpack_path):
                    self.add_log(f"Uploaded mrpack not found: {mrpack_path}", "ERROR")
                    return None
                
                return self._process_mrpack(mrpack_path, server_dir)
            else:
                self.add_log("Invalid mrpack version format", "ERROR")
                return None
                
        except Exception as e:
            self.add_log(f"Error downloading mrpack: {str(e)}", "ERROR")
            return None

    def _process_mrpack(self, mrpack_path, server_dir):
        """Обработка mrpack файла с использованием minecraft-launcher-lib"""
        try:
            self.add_log(f"Processing mrpack file: {mrpack_path}")
            
            # Устанавливаем модпак в директорию сервера
            options = {
                "skipDependenciesInstall": False
            }
            
            # Установка модпака
            minecraft_launcher_lib.mrpack.install_mrpack(
                path=mrpack_path,
                minecraft_directory=server_dir,
                mrpack_install_options=options
            )
            
            # Получаем информацию о модпаке
            modpack_info = minecraft_launcher_lib.mrpack.get_mrpack_information(mrpack_path)
            
            # Извлекаем версию Minecraft
            minecraft_version = modpack_info.get("dependencies", {}).get("minecraft")
            if minecraft_version:
                self.mrpack_minecraft_version = minecraft_version
                self.add_log(f"Modpack Minecraft version: {minecraft_version}")
            else:
                self.add_log("Could not find Minecraft version in modpack", "WARNING")
                minecraft_version = None
            
            # Определяем лоадер (Fabric/Forge и т.д.)
            loader = modpack_info.get("dependencies", {}).get("fabric-loader") or modpack_info.get("dependencies", {}).get("forge")
            if loader:
                self.add_log(f"Modpack loader: {loader}")
            
            # Проверяем наличие server.jar или аналогичного
            jar_files = [f for f in os.listdir(server_dir) if f.endswith('.jar')]
            if jar_files:
                jar_path = os.path.join(server_dir, jar_files[0])
                self.add_log(f"Found server jar: {jar_path}")
                return jar_path
            else:
                self.add_log("No server jar found after installation", "ERROR")
                return None
                
        except Exception as e:
            self.add_log(f"Error processing mrpack: {str(e)}", "ERROR")
            return None

    def create_start_script(self, jar_path):
        """Создание команды запуска в зависимости от ОС"""
        java_path = "java"  # Предполагаем, что java в PATH
        
        command = [
            java_path,
            f"-Xmx{self.memory}",
            "-jar",
            os.path.basename(jar_path),
            "nogui"
        ]
        
        if os.name == 'nt':
            return command
        else:
            return command  # Для Linux то же самое

    def monitor_output(self):
        """Мониторинг вывода консоли сервера"""
        try:
            for line in iter(self.process.stdout.readline, ''):
                line = line.strip()
                if line:
                    self.add_log(line)
                    
                    if "Done" in line and "For help" in line:
                        self.update_status("running")
                        self.add_log("Server started successfully")
                    elif "Failed to start" in line:
                        self.update_status("error")
                        self.add_log("Server failed to start")
        except Exception as e:
            self.add_log(f"Error in output monitor: {str(e)}", "ERROR")

    def stop(self):
        if not self.process or self.status == "stopped":
            return False
            
        try:
            self.process.stdin.write("stop\n")
            self.process.stdin.flush()
            
            self.status = "stopping"
            self.update_status("stopping")
            
            # Ждем завершения
            for _ in range(30):
                if self.process.poll() is not None:
                    break
                time.sleep(1)
            
            if self.process.poll() is None:
                self.process.terminate()
                time.sleep(5)
                if self.process.poll() is None:
                    self.process.kill()
            
            self.process = None
            self.status = "stopped"
            self.update_status("stopped")
            self.add_log("Server stopped")
            
            return True
            
        except Exception as e:
            self.add_log(f"Error stopping server: {str(e)}", "ERROR")
            return False

    def restart(self):
        self.stop()
        time.sleep(5)
        return self.start()

    def send_command(self, command):
        if not self.process or self.status != "running":
            return False
            
        try:
            self.process.stdin.write(f"{command}\n")
            self.process.stdin.flush()
            return True
        except Exception as e:
            self.add_log(f"Error sending command: {str(e)}", "ERROR")
            return False

def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

@contextmanager
def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS servers
            (port INTEGER PRIMARY KEY,
             version TEXT NOT NULL,
             server_type TEXT NOT NULL,
             status TEXT DEFAULT 'stopped',
             memory TEXT DEFAULT '2G',
             motd TEXT DEFAULT 'Minecraft Server',
             max_players INTEGER DEFAULT 20,
             auto_start BOOLEAN DEFAULT FALSE,
             last_started TIMESTAMP)''')
        
        cursor.execute('''CREATE TABLE IF NOT EXISTS server_logs
            (id INTEGER PRIMARY KEY AUTOINCREMENT,
             server_port INTEGER,
             timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
             log_text TEXT,
             log_level TEXT,
             FOREIGN KEY(server_port) REFERENCES servers(port))''')
        
        conn.commit()

def load_servers_from_db():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM servers")
        rows = cursor.fetchall()
        
        for row in rows:
            server = MinecraftServer(row['port'], row['version'], row['server_type'])
            server.status = row['status']
            server.memory = row['memory']
            server.motd = row['motd']
            server.max_players = row['max_players']
            server.auto_start = bool(row['auto_start'])
            
            servers[server.port] = server
            
            if server.auto_start and server.status == "stopped":
                server.start()

def check_java_installed():
    try:
        subprocess.run(["java", "-version"], capture_output=True, check=True)
        return True
    except:
        return False

@app.route('/')
def index():
    return render_template('index.html')

# Маршруты для веб-интерфейса
@app.route('/server_stats', methods=['GET'])
def server_stats():
    """Статистика серверов"""
    try:
        total_servers = len(servers)
        running_servers = sum(1 for server in servers.values() if server.status == 'running')
        stopped_servers = sum(1 for server in servers.values() if server.status == 'stopped')
        starting_servers = sum(1 for server in servers.values() if server.status == 'starting')
        stopping_servers = sum(1 for server in servers.values() if server.status == 'stopping')
        error_servers = sum(1 for server in servers.values() if server.status == 'error')
        
        return jsonify({
            "total_servers": total_servers,
            "running_servers": running_servers,
            "stopped_servers": stopped_servers,
            "starting_servers": starting_servers,
            "stopping_servers": stopping_servers,
            "error_servers": error_servers
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/list_servers', methods=['GET'])
def list_servers():
    """Список всех серверов"""
    try:
        servers_list = []
        for port, server in servers.items():
            servers_list.append({
                "port": port,
                "version": server.version,
                "server_type": server.server_type,
                "status": server.status,
                "memory": server.memory,
                "motd": server.motd,
                "max_players": server.max_players,
                "auto_start": server.auto_start
            })
        
        return jsonify({"servers": servers_list})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/available_server_types', methods=['GET'])
def available_server_types():
    """Доступные типы серверов"""
    try:
        types = []
        for key, repo in REPOSITORIES.items():
            types.append({
                "id": key,
                "name": f"{repo['name']} ({key})"
            })
        return jsonify(types)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/available_versions/<server_type>', methods=['GET'])
def available_versions(server_type):
    """Доступные версии для типа сервера"""
    try:
        if server_type not in REPOSITORIES:
            return jsonify({"error": "Invalid server type"}), 400
            
        # Для mrpack возвращаем специальное значение для загруженных файлов
        if server_type == "mrpack":
            return jsonify({"versions": ["uploaded:mrpack"]})
            
        server = MinecraftServer(99999, "temp", server_type, temporary=True)
        versions = server.get_available_versions()
        return jsonify({"versions": versions})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/repository_info/<server_type>', methods=['GET'])
def repository_info(server_type):
    """Информация о репозитории"""
    try:
        if server_type not in REPOSITORIES:
            return jsonify({"error": "Invalid server type"}), 400
            
        repo = REPOSITORIES[server_type]
        return jsonify({
            "name": repo["name"],
            "url": repo["url"],
            "type": repo["type"]
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/start_server/<int:port>', methods=['POST'])
def start_server_route(port):
    """Запуск сервера (альтернативный маршрут)"""
    return start_server(port)

@app.route('/stop_server/<int:port>', methods=['POST'])
def stop_server_route(port):
    """Остановка сервера (альтернативный маршрут)"""
    return stop_server(port)

@app.route('/create_server', methods=['POST'])
def create_server_route():
    """Создание сервера (альтернативный маршрут)"""
    try:
        data = request.json
        port = data.get('port')
        version = data.get('version')
        server_type = data.get('server_type')
        memory = data.get('memory', '2G')
        max_players = data.get('max_players', 20)
        motd = data.get('motd', 'Minecraft Server')
        auto_start = data.get('auto_start', False)
        
        if not all([port, version, server_type]):
            return jsonify({"error": "Missing required parameters"}), 400
            
        if port in servers:
            return jsonify({"error": "Port already in use"}), 400
            
        # Создаем сервер
        server = MinecraftServer(port, version, server_type)
        server.memory = memory
        server.max_players = max_players
        server.motd = motd
        server.auto_start = auto_start
        
        servers[port] = server
        
        # Запускаем сервер если auto_start=True
        if auto_start:
            server.start()
            
        return jsonify({"success": True, "message": f"Server created on port {port}"})
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/server_status/<int:port>', methods=['GET'])
def server_status_route(port):
    """Статус сервера с логами (альтернативный маршрут)"""
    server = servers.get(port)
    if not server:
        return jsonify({"error": "Server not found"}), 404
        
    # Получаем логи
    logs = []
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''SELECT log_text FROM server_logs 
            WHERE server_port = ? 
            ORDER BY id DESC LIMIT 50''', (port,))
        logs = [row['log_text'] for row in cursor.fetchall()]
    
    return jsonify({
        "status": server.status,
        "version": server.version,
        "server_type": server.server_type,
        "memory": server.memory,
        "motd": server.motd,
        "max_players": server.max_players,
        "auto_start": server.auto_start,
        "logs": logs[::-1]  # Reverse to chronological order
    })

# Основные API маршруты
@app.route('/api/servers', methods=['GET', 'POST'])
def servers_handler():
    if request.method == 'POST':
        # Create server logic
        data = request.json
        port = data.get('port')
        version = data.get('version')
        # Исправляем: используем server_type вместо type
        server_type = data.get('server_type')
        memory = data.get('memory', '2G')
        max_players = data.get('max_players', 20)
        motd = data.get('motd', 'Minecraft Server')
        auto_start = data.get('auto_start', False)
        
        if not all([port, version, server_type]):
            return jsonify({"error": "Missing parameters"}), 400
            
        if port in servers:
            return jsonify({"error": "Port already in use"}), 400
            
        try:
            # Создаем сервер с дополнительными параметрами
            server = MinecraftServer(port, version, server_type)
            server.memory = memory
            server.max_players = max_players
            server.motd = motd
            server.auto_start = auto_start
            
            # Сохраняем изменения в БД
            server._save_to_db()
            
            servers[port] = server
            
            # Если auto_start включен, запускаем сервер
            if auto_start:
                server.start()
                
            return jsonify({"success": True})
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    else:
        # Get servers logic - исправляем поле type на server_type
        servers_list = []
        for port, server in servers.items():
            servers_list.append({
                'port': port,
                'version': server.version,
                'server_type': server.server_type,  # Исправлено с type на server_type
                'status': server.status,
                'memory': server.memory,
                'motd': server.motd,
                'max_players': server.max_players,
                'auto_start': server.auto_start
            })
        return jsonify(servers_list)

@app.route('/api/create_server', methods=['POST'])
def api_create_server():
    """API endpoint для создания сервера с поддержкой mrpack"""
    try:
        data = request.json
        port = data.get('port')
        version = data.get('version')
        server_type = data.get('server_type')
        memory = data.get('memory', '2G')
        max_players = data.get('max_players', 20)
        motd = data.get('motd', 'Minecraft Server')
        auto_start = data.get('auto_start', False)
        mrpack_file_id = data.get('mrpack_file_id')
        
        if not all([port, version, server_type]):
            return jsonify({"error": "Missing required parameters: port, version, server_type"}), 400
            
        if port in servers:
            return jsonify({"error": "Port already in use"}), 400
            
        # Для mrpack серверов проверяем file_id
        if server_type == "mrpack" and not mrpack_file_id and not version.startswith("uploaded:"):
            return jsonify({"error": "For mrpack servers, mrpack_file_id or uploaded version is required"}), 400
            
        # Создаем сервер
        server = MinecraftServer(port, version, server_type)
        server.memory = memory
        server.max_players = max_players
        server.motd = motd
        server.auto_start = auto_start
        
        servers[port] = server
        
        # Запускаем сервер если auto_start=True
        if auto_start:
            server.start()
            
        return jsonify({"success": True, "message": f"Server created on port {port}"})
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/server/<int:port>/start', methods=['POST'])
def start_server(port):
    server = servers.get(port)
    if not server:
        return jsonify({"error": "Server not found"}), 404
        
    if server.start():
        return jsonify({"success": True})
    else:
        return jsonify({"error": "Failed to start server"}), 500

@app.route('/api/server/<int:port>/stop', methods=['POST'])
def stop_server(port):
    server = servers.get(port)
    if not server:
        return jsonify({"error": "Server not found"}), 404
        
    if server.stop():
        return jsonify({"success": True})
    else:
        return jsonify({"error": "Failed to stop server"}), 500

@app.route('/api/server/<int:port>/restart', methods=['POST'])
def restart_server(port):
    server = servers.get(port)
    if not server:
        return jsonify({"error": "Server not found"}), 404
        
    if server.restart():
        return jsonify({"success": True})
    else:
        return jsonify({"error": "Failed to restart server"}), 500

@app.route('/api/server/<int:port>/command', methods=['POST'])
def send_command(port):
    server = servers.get(port)
    if not server:
        return jsonify({"error": "Server not found"}), 404
        
    data = request.json
    command = data.get('command')
    
    if not command:
        return jsonify({"error": "Command required"}), 400
        
    if server.send_command(command):
        return jsonify({"success": True})
    else:
        return jsonify({"error": "Failed to send command"}), 500

@app.route('/api/server/<int:port>/logs', methods=['GET'])
def get_logs(port):
    limit = request.args.get('limit', 50, type=int)
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''SELECT log_text FROM server_logs 
            WHERE server_port = ? 
            ORDER BY id DESC LIMIT ?''', (port, limit))
        logs = [row['log_text'] for row in cursor.fetchall()]
        
    return jsonify(logs[::-1])  # Reverse to chronological order

@app.route('/api/server/<int:port>/status', methods=['GET'])
def get_status(port):
    server = servers.get(port)
    if not server:
        return jsonify({"error": "Server not found"}), 404
        
    return jsonify({
        "status": server.status,
        "version": server.version,
        "server_type": server.server_type,
        "memory": server.memory,
        "motd": server.motd,
        "max_players": server.max_players,
        "auto_start": server.auto_start
    })

@app.route('/api/server/<int:port>/settings', methods=['POST'])
def update_settings(port):
    server = servers.get(port)
    if not server:
        return jsonify({"error": "Server not found"}), 404
        
    data = request.json
    if 'memory' in data:
        server.memory = data['memory']
    if 'motd' in data:
        server.motd = data['motd']
    if 'max_players' in data:
        server.max_players = data['max_players']
    if 'auto_start' in data:
        server.auto_start = bool(data['auto_start'])
        
    server._save_to_db()
    return jsonify({"success": True})

@app.route('/api/server/<int:port>/delete', methods=['POST'])
def delete_server(port):
    server = servers.get(port)
    if not server:
        return jsonify({"error": "Server not found"}), 404
        
    if server.status != "stopped":
        server.stop()
        
    try:
        server_dir = get_full_path("servers", str(port))
        if os.path.exists(server_dir):
            shutil.rmtree(server_dir)
            
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM servers WHERE port = ?", (port,))
            cursor.execute("DELETE FROM server_logs WHERE server_port = ?", (port,))
            conn.commit()
            
        del servers[port]
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/repositories', methods=['GET'])
def get_repositories():
    return jsonify(list(REPOSITORIES.keys()))

@app.route('/api/versions/<server_type>', methods=['GET'])
def get_versions(server_type):
    if server_type not in REPOSITORIES:
        return jsonify({"error": "Invalid server type"}), 400
        
    # Для mrpack возвращаем placeholder
    if server_type == "mrpack":
        return jsonify(["uploaded:mrpack"])
        
    server = MinecraftServer(99999, "temp", server_type, temporary=True)  # Temp server for versions
    versions = server.get_available_versions()
    return jsonify(versions)

# File management routes
@app.route('/server_files/<int:port>')
def server_files(port):
    server = servers.get(port)
    if not server:
        return jsonify({"error": "Server not found"}), 404
        
    path = request.args.get('path', '')
    full_path = os.path.join(get_full_path("servers", str(port)), path)
    
    if not os.path.exists(full_path):
        return jsonify({"error": "Path not found"}), 404
        
    if os.path.isdir(full_path):
        files = []
        for item in os.listdir(full_path):
            item_path = os.path.join(full_path, item)
            files.append({
                "name": item,
                "type": "directory" if os.path.isdir(item_path) else "file",
                "size": os.path.getsize(item_path) if os.path.isfile(item_path) else 0
            })
        return jsonify({"files": files})
    else:
        return jsonify({"error": "Not a directory"}), 400

@app.route('/server_files/<int:port>/content')
def server_file_content(port):
    server = servers.get(port)
    if not server:
        return jsonify({"error": "Server not found"}), 404
        
    file_path = request.args.get('file')
    if not file_path:
        return jsonify({"error": "File parameter required"}), 400
        
    full_path = os.path.join(get_full_path("servers", str(port)), file_path)
    
    if not os.path.exists(full_path) or os.path.isdir(full_path):
        return jsonify({"error": "File not found"}), 404
        
    try:
        # Пробуем разные кодировки
        encodings = ['utf-8', 'latin-1', 'cp1252', 'iso-8859-1']
        for encoding in encodings:
            try:
                with open(full_path, 'r', encoding=encoding) as f:
                    content = f.read()
                return jsonify({"content": content})
            except UnicodeDecodeError:
                continue
        return jsonify({"error": "Unable to decode file content"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/server_files/<int:port>/download')
def server_file_download(port):
    """Скачивание файла"""
    server = servers.get(port)
    if not server:
        return jsonify({"error": "Server not found"}), 404
        
    file_path = request.args.get('file')
    if not file_path:
        return jsonify({"error": "File parameter required"}), 400
        
    full_path = os.path.join(get_full_path("servers", str(port)), file_path)
    
    if not os.path.exists(full_path) or os.path.isdir(full_path):
        return jsonify({"error": "File not found"}), 404
        
    try:
        return send_file(full_path, as_attachment=True)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/server_files/<int:port>/save', methods=['POST'])
def server_file_save(port):
    """Сохранение файла"""
    server = servers.get(port)
    if not server:
        return jsonify({"error": "Server not found"}), 404
        
    data = request.get_json()
    if not data:
        return jsonify({"error": "No JSON data provided"}), 400
        
    file_path = data.get('path')
    content = data.get('content')
    
    if not file_path or content is None:
        return jsonify({"error": "Path and content required"}), 400
        
    full_path = os.path.join(get_full_path("servers", str(port)), file_path)
    
    try:
        # Создаем директорию
        dir_path = os.path.dirname(full_path)
        os.makedirs(dir_path, exist_ok=True)
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/server_files/<int:port>/create_file', methods=['POST'])
def server_create_file(port):
    """Создание нового файла"""
    server = servers.get(port)
    if not server:
        return jsonify({"error": "Server not found"}), 404
        
    data = request.get_json()
    if not data:
        return jsonify({"error": "No JSON data provided"}), 400
        
    file_path = data.get('path')
    
    if not file_path:
        return jsonify({"error": "Path required"}), 400
        
    full_path = os.path.join(get_full_path("servers", str(port)), file_path)
    
    try:
        # Создаем директорию
        dir_path = os.path.dirname(full_path)
        os.makedirs(dir_path, exist_ok=True)
        with open(full_path, 'w') as f:
            f.write('')
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/server_files/<int:port>/create_folder', methods=['POST'])
def server_create_folder(port):
    """Создание новой папки"""
    server = servers.get(port)
    if not server:
        return jsonify({"error": "Server not found"}), 404
        
    data = request.get_json()
    if not data:
        return jsonify({"error": "No JSON data provided"}), 400
        
    folder_path = data.get('path')
    
    if not folder_path:
        return jsonify({"error": "Path required"}), 400
        
    full_path = os.path.join(get_full_path("servers", str(port)), folder_path)
    
    try:
        os.makedirs(full_path, exist_ok=True)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/server_files/<int:port>/delete', methods=['POST'])
def server_delete_file(port):
    """Удаление файла или папки"""
    server = servers.get(port)
    if not server:
        return jsonify({"error": "Server not found"}), 404
        
    data = request.get_json()
    if not data:
        return jsonify({"error": "No JSON data provided"}), 400
        
    path = data.get('path')
    
    if not path:
        return jsonify({"error": "Path required"}), 400
        
    full_path = os.path.join(get_full_path("servers", str(port)), path)
    
    if not os.path.exists(full_path):
        return jsonify({"error": "Path not found"}), 404
        
    try:
        if os.path.isdir(full_path):
            shutil.rmtree(full_path)
        else:
            os.remove(full_path)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/server_files/<int:port>/upload', methods=['POST'])
def server_upload_file(port):
    """Загрузка файла на сервер"""
    server = servers.get(port)
    if not server:
        return jsonify({"error": "Server not found"}), 404
        
    if 'file' not in request.files:
        return jsonify({"error": "No file provided"}), 400
        
    file = request.files['file']
    path = request.form.get('path', '')
    
    if file.filename == '':
        return jsonify({"error": "No file selected"}), 400
        
    server_dir = get_full_path("servers", str(port))
    if path:
        upload_dir = os.path.join(server_dir, path)
    else:
        upload_dir = server_dir
        
    os.makedirs(upload_dir, exist_ok=True)
    
    try:
        file_path = os.path.join(upload_dir, file.filename)
        file.save(file_path)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Новый маршрут для загрузки .mrpack файлов
@app.route('/upload_mrpack', methods=['POST'])
def upload_mrpack():
    """Загрузка и обработка .mrpack файла"""
    try:
        if 'mrpack_file' not in request.files:
            return jsonify({"error": "No file provided"}), 400
            
        file = request.files['mrpack_file']
        if file.filename == '':
            return jsonify({"error": "No file selected"}), 400
            
        if not file.filename.endswith('.mrpack'):
            return jsonify({"error": "File must be a .mrpack file"}), 400
        
        # Создаем директорию для загрузок, если не существует
        uploads_dir = get_full_path("uploads")
        os.makedirs(uploads_dir, exist_ok=True)
        
        # Генерируем уникальное имя файла
        file_id = str(uuid.uuid4())
        filename = f"{file_id}.mrpack"
        file_path = os.path.join(uploads_dir, filename)
        
        # Сохраняем файл
        file.save(file_path)
        
        logger.info(f"Saved uploaded mrpack file: {file_path}")
        
        # Создаем временный сервер для обработки mrpack
        temp_server = MinecraftServer(99999, f"uploaded:{filename}", "mrpack", temporary=True)
        
        # Обрабатываем mrpack файл для получения информации
        server_dir = get_full_path("temp_processing")
        os.makedirs(server_dir, exist_ok=True)
        
        try:
            modpack_info = minecraft_launcher_lib.mrpack.get_mrpack_information(file_path)
            minecraft_version = modpack_info.get("dependencies", {}).get("minecraft", "Unknown")
            
            # Получаем информацию о файле
            file_size = os.path.getsize(file_path)
            
            # Очищаем временную директорию
            if os.path.exists(server_dir):
                shutil.rmtree(server_dir)
            
            return jsonify({
                "success": True,
                "file_id": filename,
                "minecraft_version": minecraft_version,
                "file_size": file_size,
                "original_filename": file.filename,
                "version": f"uploaded:{filename}"
            })
            
        except Exception as e:
            # Очищаем в случае ошибки
            if os.path.exists(server_dir):
                shutil.rmtree(server_dir)
            logger.error(f"Error processing mrpack file: {str(e)}")
            return jsonify({"error": f"Failed to process mrpack file: {str(e)}"}), 500
            
    except Exception as e:
        logger.error(f"Error uploading mrpack: {e}")
        return jsonify({"error": f"Failed to upload mrpack file: {str(e)}"}), 500

# Очистка при завершении
def cleanup():
    for port, server in servers.items():
        if port == 99999:
            continue
            
        if server.process:
            server.stop()

atexit.register(cleanup)

if __name__ == '__main__':
    # Проверка наличия Java
    if not check_java_installed():
        logger.error("Java is not installed or not in PATH")
        print("ERROR: Java is not installed or not in PATH")
        exit(1)
    
    # Инициализация базы данных
    init_db()
    
    # Создаем директории
    servers_dir = get_full_path("servers")
    uploads_dir = get_full_path("uploads")
    if not os.path.exists(servers_dir):
        os.makedirs(servers_dir)
    if not os.path.exists(uploads_dir):
        os.makedirs(uploads_dir)
    
    # Загружаем серверы из базы данных
    load_servers_from_db()
    
    # Запускаем Flask приложение
    logger.info("Minecraft Server Manager запущен")
    logger.info(f"Base directory: {BASE_DIR}")
    app.run(host='0.0.0.0', port=5000, debug=True)