# SkyrimNet Master Debug Proxy - thin shim over server.py.
#
# Prior to the audit this file was a byte-for-byte duplicate of server.py
# (only the header comment differed). That meant every fix had to be applied
# twice and the two copies drifted in practice. The whole bridge now lives in
# ``server.py``; this script exists only to keep ``run_debug.bat`` working and
# to enable verbose logging.
from server import serve


if __name__ == "__main__":
    serve(debug=True)
