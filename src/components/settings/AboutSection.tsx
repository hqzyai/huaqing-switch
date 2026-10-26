import { useEffect, useState } from "react";
import { ExternalLink, Info } from "lucide-react";
import { useTranslation } from "react-i18next";
import { getVersion } from "@tauri-apps/api/app";
import { settingsApi } from "@/lib/api";
import { BRAND } from "@/lib/brand";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import logo from "@/assets/branding/hqzyai-logomark.svg";

interface AboutSectionProps {
  isPortable: boolean;
}

/** Product identity and upstream attribution are intentionally separate. */
export function AboutSection({ isPortable }: AboutSectionProps) {
  const { t } = useTranslation();
  const [version, setVersion] = useState(BRAND.version);
  useEffect(() => {
    let active = true;
    void getVersion()
      .then((value) => {
        if (active) setVersion(value);
      })
      .catch(() => {
        /* Browser-based UI validation has no native app version. */
      });
    return () => {
      active = false;
    };
  }, []);

  return (
    <section
      className="divide-y divide-border rounded-panel border border-border bg-surface"
      aria-label={BRAND.productName}
    >
      <div className="flex flex-wrap items-center gap-4 p-5">
        <img
          src={logo}
          alt="HQZY"
          draggable={false}
          className="h-7 w-7 shrink-0 object-contain"
        />
        <div className="min-w-0 flex-1 space-y-1">
          <h2 className="text-title text-fg-1">{BRAND.productName}</h2>
          <p className="text-body text-fg-2">{t("branding.description")}</p>
        </div>
        <Badge variant="secondary">v{version}</Badge>
        {isPortable && (
          <Badge variant="outline">{t("settings.portableMode")}</Badge>
        )}
      </div>
      <div
        className="flex items-start gap-2 p-5 text-body text-fg-2"
        role="status"
      >
        <Info aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0" />
        <p>{t("branding.updatesUnavailable")}</p>
      </div>
      <div className="space-y-4 p-5">
        <p className="text-body text-fg-2">{t("branding.attribution")}</p>
        <Button
          type="button"
          variant="outline"
          size="compact"
          onClick={() => {
            void settingsApi.openExternal(BRAND.upstream.repository);
          }}
        >
          <ExternalLink aria-hidden="true" className="h-4 w-4" />
          {t("branding.upstreamSource")}
        </Button>
        <p className="text-caption text-fg-2">{t("branding.license")}</p>
      </div>
    </section>
  );
}
