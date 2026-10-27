import uvicorn

from .engine import load_env


def main():
    env = load_env()
    port = int(env.get("PORT") or 8787)
    uvicorn.run("backend.api:app", host="127.0.0.1", port=port, workers=1, reload=False)


if __name__ == "__main__":
    main()
