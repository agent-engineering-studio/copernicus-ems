import type { Metadata, Viewport } from "next";
// Bootstrap Italia, the kit of the Italian public administration: served from here, no CDN.
import "bootstrap-italia/dist/css/bootstrap-italia.min.css";
import "leaflet/dist/leaflet.css";
import "./globals.css";

export const metadata: Metadata = {
  description:
    "The demo of copernicus-ems: after a flood, where the satellites of Copernicus EMS looked and where they saw water.",
};

export const viewport: Viewport = { width: "device-width", initialScale: 1 };

export default function Layout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
