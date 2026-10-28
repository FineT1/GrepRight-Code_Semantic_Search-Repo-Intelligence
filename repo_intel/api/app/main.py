from .routes import repos, search, stats
app.include_router(search.router)
app.include_router(stats.router)