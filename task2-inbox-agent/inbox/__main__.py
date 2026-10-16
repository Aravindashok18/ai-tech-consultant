import uvicorn

if __name__ == "__main__":
    uvicorn.run("inbox.api:create_app", factory=True, host="0.0.0.0", port=8001)
