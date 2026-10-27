use huaqing_switch_server::{config::Config, App};
use std::net::SocketAddr;
use tokio::{net::TcpListener, sync::watch, task::JoinSet};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    if std::env::args()
        .nth(1)
        .is_some_and(|arg| arg == "healthcheck" || arg == "--healthcheck")
    {
        return healthcheck().await;
    }
    let config = Config::from_env()?;
    // Bind all sockets before accepting requests; an optional-port conflict fails the process.
    let secure_listener = TcpListener::bind(config.web_bind).await?;
    let http_listener = match &config.http {
        Some(http) => Some(TcpListener::bind(http.bind).await?),
        None => None,
    };
    let app = App::open(config)?;
    app.start_gateway().await?;
    let (stop, receive) = watch::channel(false);
    let mut servers = JoinSet::new();
    spawn_web(&mut servers, secure_listener, app.web(), receive.clone());
    if let Some(listener) = http_listener {
        spawn_web(
            &mut servers,
            listener,
            app.http_web().expect("validated HTTP configuration"),
            receive,
        );
    }
    println!("Huaqing Switch server ready");
    let unexpected = tokio::select! {
        result = servers.join_next() => Some(result),
        _ = shutdown_signal() => None,
    };
    let _ = stop.send(true);
    app.shutdown().await;
    while let Some(result) = servers.join_next().await {
        result??;
    }
    if let Some(result) = unexpected {
        if let Some(result) = result {
            result??;
        }
        anyhow::bail!("management listener stopped unexpectedly");
    }
    println!("Huaqing Switch server stopped");
    Ok(())
}
fn spawn_web(
    servers: &mut JoinSet<std::io::Result<()>>,
    listener: TcpListener,
    router: axum::Router,
    mut shutdown: watch::Receiver<bool>,
) {
    servers.spawn(async move {
        axum::serve(
            listener,
            router.into_make_service_with_connect_info::<SocketAddr>(),
        )
        .with_graceful_shutdown(async move {
            let stopped = *shutdown.borrow();
            if !stopped {
                let _ = shutdown.changed().await;
            }
        })
        .await
    });
}
async fn healthcheck() -> anyhow::Result<()> {
    let mut binds: Vec<SocketAddr> = vec![std::env::var("HQ_SWITCH_WEB_BIND")
        .unwrap_or_else(|_| "0.0.0.0:8080".into())
        .parse()?];
    match (
        std::env::var("HQ_SWITCH_HTTP_ORIGIN").ok(),
        std::env::var("HQ_SWITCH_HTTP_BIND").ok(),
    ) {
        (None, None) => {}
        (Some(_), Some(bind)) => binds.push(bind.parse()?),
        _ => anyhow::bail!("HTTP management origin and listener must be configured together"),
    }
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(3))
        .no_proxy()
        .build()?;
    for bind in binds {
        let address = if bind.ip().is_unspecified() {
            SocketAddr::new(
                if bind.is_ipv4() {
                    std::net::Ipv4Addr::LOCALHOST.into()
                } else {
                    std::net::Ipv6Addr::LOCALHOST.into()
                },
                bind.port(),
            )
        } else {
            bind
        };
        let response = client
            .get(format!("http://{address}/healthz"))
            .send()
            .await?;
        if !response.status().is_success() {
            anyhow::bail!("management listener is not healthy");
        }
    }
    Ok(())
}
async fn shutdown_signal() {
    #[cfg(unix)]
    {
        let mut terminate =
            tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
                .expect("SIGTERM handler");
        tokio::select! { _=tokio::signal::ctrl_c()=>{}, _=terminate.recv()=>{} }
    }
    #[cfg(not(unix))]
    {
        let _ = tokio::signal::ctrl_c().await;
    }
}
