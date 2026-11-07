from dotenv import load_dotenv
load_dotenv()
import uvicorn
import os
uvicorn.run('app.main:app',host='0.0.0.0',port=int(os.getenv('PORT','8000')),reload=False,
            proxy_headers=True,forwarded_allow_ips=os.getenv('FORWARDED_ALLOW_IPS','127.0.0.1'))
