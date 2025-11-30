import azure.functions as func
from main import app as fastapi_app

app = func.FunctionApp()

@app.route(route="{*route}", auth_level=func.AuthLevel.ANONYMOUS)
async def http_app_func(req: func.HttpRequest, context: func.Context) -> func.HttpResponse:
    return await func.AsgiMiddleware(fastapi_app).handle_async(req, context)
