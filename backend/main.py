from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def read_root():
    return {"message": "check"}

@app.get("/api/auth/google")
async def login(req):
    pass