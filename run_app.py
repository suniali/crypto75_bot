from waitress import serve
from config.wsgi import application 
from decouple import config

PORT = config("DASHBOARD_PORT", default=8000)

if __name__ == '__main__':
    print("="*50)
    print(" WaveScore Trading Journal is Running locally...")
    print(f" Open your browser and go to: http://0.0.0.0:{PORT}")
    print("="*50)
    
    serve(application, host='0.0.0.0', port=PORT)