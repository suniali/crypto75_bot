from waitress import serve
from config.wsgi import application
from decouple import config

PORT = int(config("DASHBOARD_PORT", default=8000))
HTTPS_PORT = int(config("HTTPS_PORT", default=8443))

if __name__ == '__main__':
    print("=" * 50)
    print(" WaveScore Trading Journal Backend (Waitress) Running...")
    print(f" Internal HTTP : http://127.0.0.1:{PORT}")
    print(f" Secure HTTPS  : https://localhost:{HTTPS_PORT}")
    print("=" * 50)

    serve(application, host='127.0.0.1', port=PORT)