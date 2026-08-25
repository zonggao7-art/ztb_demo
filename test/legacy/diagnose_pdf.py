"""诊断脚本：检查 PDF 可读性和 magic-pdf 环境"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PDF_DIR = os.path.join(PROJECT_ROOT, "raw_pdfs")

print("=" * 60)
print("1. PDF 文件检查")
print("=" * 60)

pdf_files = [f for f in os.listdir(PDF_DIR) if f.lower().endswith(".pdf")]
print(f"PDF 数量: {len(pdf_files)}")

for fname in pdf_files:
    fpath = os.path.join(PDF_DIR, fname)
    size_mb = os.path.getsize(fpath) / (1024 * 1024)
    # 读取文件头
    with open(fpath, "rb") as f:
        header = f.read(1024)
    is_pdf = header[:5] == b"%PDF-"
    # 检查加密（读全文太慢，只查前 64KB）
    with open(fpath, "rb") as f:
        chunk = f.read(65536)
    encrypted = b"/Encrypt" in chunk
    print(f"\n  文件: {fname}")
    print(f"  大小: {size_mb:.1f} MB")
    print(f"  文件头: {header[:20]}")
    print(f"  是有效PDF: {is_pdf}")
    print(f"  前64KB含加密标记: {encrypted}")

# 尝试用 fitz (PyMuPDF) 读取页数
print("\n" + "=" * 60)
print("2. PyMuPDF (fitz) 读取测试")
print("=" * 60)
try:
    import fitz
    print(f"PyMuPDF 版本: {fitz.__version__}")
    for fname in pdf_files:
        fpath = os.path.join(PDF_DIR, fname)
        try:
            doc = fitz.open(fpath)
            print(f"  {fname[:30]}... → {doc.page_count} 页, 需密码={doc.needs_pass}")
            # 尝试提取第一页文本
            page0_text = doc[0].get_text()[:200]
            print(f"    第1页文本前200字: {repr(page0_text[:100])}")
            doc.close()
        except Exception as e:
            print(f"  {fname[:30]}... → 打开失败: {e}")
except ImportError:
    print("PyMuPDF (fitz) 未安装, 跳过")

# 检查 magic-pdf 环境
print("\n" + "=" * 60)
print("3. magic-pdf 环境检查")
print("=" * 60)

# 3a. Python API
try:
    import magic_pdf
    print(f"magic_pdf 可导入, 版本: {getattr(magic_pdf, '__version__', 'unknown')}")
    print(f"  模块路径: {magic_pdf.__file__}")
except ImportError as e:
    print(f"magic_pdf 导入失败: {e}")

# 3b. CLI
import shutil
import subprocess

magic_exe = shutil.which("magic-pdf")
print(f"\nmagic-pdf CLI (which): {magic_exe}")

# 检查 conda Scripts 目录
scripts_dir = os.path.join(os.path.dirname(sys.executable), "Scripts")
magic_in_scripts = os.path.join(scripts_dir, "magic-pdf.exe")
print(f"Scripts目录 magic-pdf.exe: {magic_in_scripts} → 存在={os.path.isfile(magic_in_scripts)}")

# 尝试运行 --version
for exe in [magic_exe, magic_in_scripts]:
    if exe and os.path.isfile(exe):
        try:
            r = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=10)
            print(f"  {exe} --version → rc={r.returncode}, out={r.stdout.strip()}, err={r.stderr.strip()[:200]}")
        except Exception as e:
            print(f"  {exe} 执行失败: {e}")
        break

# 3c. 模型目录
MINERU_ROOT = r"D:\MinerU"
models_dir = os.path.join(MINERU_ROOT, "models")
print(f"\n模型目录: {models_dir}")
print(f"  存在: {os.path.isdir(models_dir)}")
if os.path.isdir(models_dir):
    items = os.listdir(models_dir)
    print(f"  内容 ({len(items)} 项): {items[:20]}")
    # 检查是否有实际模型文件
    total_size = 0
    file_count = 0
    for root, dirs, files in os.walk(models_dir):
        for f in files:
            fp = os.path.join(root, f)
            total_size += os.path.getsize(fp)
            file_count += 1
    print(f"  模型文件总数: {file_count}, 总大小: {total_size/1024/1024:.1f} MB")
else:
    print("  *** 模型目录不存在! 这是解析返回空结果的最可能原因 ***")

print("\n" + "=" * 60)
print("4. 诊断结论")
print("=" * 60)
