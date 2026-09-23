import type { Metadata } from "next";
import "./globals.css";
import { Nav } from "@/components/Nav";

export const metadata: Metadata = {
  title: "ForexWizard AI Market Brain",
  description: "Experimental XAU/USD market intelligence terminal with persistent local memory.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body><div className="app-shell"><Nav /><main className="main-area">{children}</main></div></body></html>;
}
