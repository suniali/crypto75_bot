import os
from waitress import serve
from config.wsgi import application 

if __name__ == '__main__':
    print("="*50)
    print(" WaveScore Trading Journal is Running locally...")
    print(" Open your browser and go to: http://127.0.0.1:8000")
    print("="*50)
    
    # اجرای وب‌سرور روی پورت ۸۰۰۰
    serve(application, host='127.0.0.1', port=8000)