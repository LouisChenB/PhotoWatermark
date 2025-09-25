import os
import subprocess
import sys

# 确保在当前目录运行
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# 构建命令
cmd = [
    sys.executable,
    '-m', 'PyInstaller',
    '--onefile',       # 生成单个可执行文件
    '--windowed',      # 无控制台窗口
    '--name', '水印工具',  # 应用名称
    # 移除不存在的图标引用，如果有图标文件再加回来
    'watermark.py'     # 主程序文件
]

# 执行打包命令
try:
    subprocess.run(cmd, check=True)
    print("打包成功！可执行文件位于 dist 目录下")
except subprocess.CalledProcessError as e:
    print(f"打包失败：{e}")
    sys.exit(1)