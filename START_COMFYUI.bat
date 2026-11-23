@echo off
cd /d "C:\Users\haqury\comfyui-amd-windows-setup\ComfyUI"
REM Новый ComfyUI: --fp32 из launch.py конфликтует; используем force-fp32 + DirectML (AMD)
python main.py --listen 0.0.0.0 --port 8188 --directml --force-fp32
pause