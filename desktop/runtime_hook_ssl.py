# desktop/runtime_hook_ssl.py
# 职责：打包后修正 SSL 证书路径
# PyInstaller 冻结环境下 certifi 的相对路径会失效，这里显式指向随包分发的 cacert.pem

import os
import sys

if getattr(sys, "frozen", False):
    base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    cacert = os.path.join(base, "cacert.pem")
    if os.path.exists(cacert):
        os.environ["SSL_CERT_FILE"] = cacert
        os.environ["REQUESTS_CA_BUNDLE"] = cacert
        os.environ["CURL_CA_BUNDLE"] = cacert