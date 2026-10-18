#!/usr/bin/env python3
"""查看使用帮助，不初始化目录、不读取个人数据。实际操作使用 commons.py。"""
import sys
sys.dont_write_bytecode = True
from commons import main

if __name__ == '__main__':
    sys.exit(main(['--help']))
