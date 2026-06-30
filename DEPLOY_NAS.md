# StuLink 部署指南 — 绿联 NAS (Docker)

## 前置条件

- Windows 电脑已安装 Docker Desktop
- NAS 已开启 SSH（绿联 UGOS：设置 → 终端机 → 启用 SSH）
- NAS 已安装 Docker 套件（绿联 UGOS：应用中心 → Docker）

## 第一步：Windows 上构建镜像

在项目根目录下打开 PowerShell，执行：

```powershell
# 构建镜像（使用清华镜像源加速 pip）
docker build -t stulink:v1 .

# 导出镜像为 tar 文件
docker save stulink:v1 -o stulink.tar

# 同时准备好项目代码（不含 data/ 和 .git/）
# 整个项目文件夹就是 dormitory-code/ 的内容
```

## 第二步：NAS 上创建目录结构

通过 SSH 连接到 NAS（或用绿联 Docker 界面）：

```bash
# SSH 登录 NAS（默认用户名 root，端口 922）
ssh root@你的NAS的IP -p 922

# 创建部署目录
mkdir -p /volume1/docker/stulink
cd /volume1/docker/stulink

# 创建数据和代码目录
mkdir dormitory-code dormitory-data
```

## 第三步：上传文件到 NAS

通过 SMB 共享（Windows 资源管理器地址栏输入 `\\你的NAS的IP`）：

将以下文件上传到 NAS 的 `/volume1/docker/stulink/` 目录：

1. **stulink.tar** → 上传到 `/volume1/docker/stulink/`
2. **项目代码**（整个项目文件夹内容） → 上传到 `/volume1/docker/stulink/dormitory-code/`
3. **docker-compose.yml** → 上传到 `/volume1/docker/stulink/`

> ⚠ **不要**上传 `data/` 目录和 `.git/` 目录

## 第四步：NAS 上导入镜像并启动

SSH 登录 NAS：

```bash
cd /volume1/docker/stulink

# 导入 Docker 镜像
docker load -i stulink.tar

# 启动容器（后台运行）
docker-compose up -d

# 查看是否启动成功
docker ps
# 应看到 stulink 容器状态为 Up
```

## 第五步：确认部署

浏览器访问 `http://你的NAS的IP:5000`，应看到登录页面。

默认管理员：`admin` / `admin123`

---

## 升级（后续更新代码时）

```bash
# SSH 登录 NAS
cd /volume1/docker/stulink

# 停服务
docker-compose down

# 通过 SMB 覆盖 dormitory-code/ 下所有文件
# 注意：dormitory-data/ 绝对不能动！

# 重新构建镜像（如果有新的 Python 依赖）
docker build -t stulink:v2 .
# 并更新 docker-compose.yml 中的 image 为 stulink:v2

# 如果只是代码变更（没有新依赖），可直接：
docker-compose up -d
```

---

## 重要说明

| 目录 | 内容 | 升级时 |
|------|------|--------|
| `dormitory-code/` | 项目代码 | 整个覆盖 |
| `dormitory-data/` | 数据库 + 密钥文件 | 永不覆盖 |
| `dormitory-data/dormitory.db` | SQLite 数据库 | 持久保存 |
| `dormitory-data/.secret_key` | Flask 密钥（自动生成） | 持久保存 |
| `dormitory-data/.encryption_key` | 身份证加密密钥（自动生成） | 持久保存 |
| `dormitory-data/backups/` | 数据备份 | 持久保存 |

---

## 故障排查

**Q: 容器启动后立即退出？**
```bash
docker logs stulink   # 查看错误日志
```

**Q: 5000 端口被占用？**
修改 `docker-compose.yml` 中 `ports` 为其他端口，如 `"8080:5000"`。

**Q: 数据库被锁定？**
SQLite 仅支持单进程。确保只有一个 stulink 容器在运行。

**Q: 忘记 admin 密码？**
删除 `dormitory-data/dormitory.db` 后重新运行 `python scripts/init_db.py`（会丢失所有数据）。
