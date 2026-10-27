import React from "react";
import ReactDOM from "react-dom/client";
import { MotionConfig } from "framer-motion";
import { ServerApp } from "./components/hq/ServerApp";
import { ThemeProvider } from "./components/theme-provider";
import { TooltipProvider } from "./components/ui/tooltip";
import { Toaster } from "./components/ui/sonner";
import { Button } from "./components/ui/button";
import { initializeInputModality } from "./lib/inputModality";
import "./index.css";
import "./i18n";

class ServerErrorBoundary extends React.Component<
  React.PropsWithChildren,
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    return this.state.failed ? (
      <main className="flex min-h-screen items-center justify-center bg-app p-6 text-fg-1">
        <section
          role="alert"
          className="space-y-4 rounded-panel border border-border bg-card p-6"
        >
          <h1 className="text-title">界面遇到了问题</h1>
          <p className="text-body text-fg-2">
            请重新加载页面。服务器保存的数据不受影响。
          </p>
          <Button onClick={() => window.location.reload()}>重新加载</Button>
        </section>
      </main>
    ) : (
      this.props.children
    );
  }
}

initializeInputModality();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ServerErrorBoundary>
      <ThemeProvider syncNativeTheme={false}>
        <MotionConfig reducedMotion="user">
          <TooltipProvider delayDuration={0} skipDelayDuration={0}>
            <ServerApp />
          </TooltipProvider>
          <Toaster />
        </MotionConfig>
      </ThemeProvider>
    </ServerErrorBoundary>
  </React.StrictMode>,
);
