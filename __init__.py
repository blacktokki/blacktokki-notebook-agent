from env import ROOT_PATH
from mcp_server import mcp

if __name__ == "__main__":
    mcp.run(transport="sse", uvicorn_config={"root_path": ROOT_PATH})
