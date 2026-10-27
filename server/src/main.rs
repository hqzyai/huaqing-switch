use huaqing_switch_server::{config::Config, App};
#[tokio::main]
async fn main() -> anyhow::Result<()> {
    if std::env::args()
        .nth(1)
        .is_some_and(|arg| arg == "healthcheck" || arg == "--healthcheck")
    {
        let bind: std::net::SocketAddr = std::env::var("HQ_SWITCH_WEB_BIND")
            .unwrap_or_else(|_| "0.0.0.0:8080".into())
            .parse()?;
        let response = reqwest::Client::builder()
            .timeout(std::time::Duration::from_secs(3))
            .no_proxy()
            .build()?
            .get(format!("http://127.0.0.1:{}/healthz", bind.port()))
            .send()
            .await?;
        if !response.status().is_success() {
            anyhow::bail!("service is not healthy");
        }
        return Ok(());
    }
    let config = Config::from_env()?;
    let listener = tokio::net::TcpListener::bind(config.web_bind).await?;
    let app = App::open(config)?;
    app.start_gateway().await?;
    println!("Huaqing Switch server ready");
    let (stop, receive) = tokio::sync::oneshot::channel();
    let web = app.web();
    let mut server = tokio::spawn(async move {
        axum::serve(
            listener,
            web.into_make_service_with_connect_info::<std::net::SocketAddr>(),
        )
        .with_graceful_shutdown(async {
            let _ = receive.await;
        })
        .await
    });
    tokio::select! {
     result=&mut server=>{app.shutdown().await;result??;return Ok(());}
     _=shutdown_signal()=>{}
    }
    let _ = stop.send(());
    app.shutdown().await;
    server.await??;
    println!("Huaqing Switch server stopped");
    Ok(())
}
async fn shutdown_signal() {
    #[cfg(unix)]
    {
        let mut terminate =
            tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
                .expect("SIGTERM handler");
        tokio::select! {_=tokio::signal::ctrl_c()=>{},_=terminate.recv()=>{}}
    }
    #[cfg(not(unix))]
    {
        let _ = tokio::signal::ctrl_c().await;
    }
}
