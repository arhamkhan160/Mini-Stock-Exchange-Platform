import type { Metadata } from "next";
import "./globals.css";
import { AuthProvider } from "@/lib/auth";
import { ToastProvider } from "@/components/Toast";
import Navbar from "@/components/Navbar";

export const metadata: Metadata = {
  title: "Mini Stock Exchange",
  description: "A microservices-based paper trading exchange — SWE 4602",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-bg text-ink antialiased">
        <AuthProvider>
          <ToastProvider>
            <a
              href="#content"
              className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50
                focus:rounded-md focus:bg-panel2 focus:px-3 focus:py-2 focus:text-sm focus:text-ink"
            >
              Skip to content
            </a>
            <Navbar />
            {/* The one page container. Pages must NOT re-wrap in their own
                max-w/px — doing so nests the width and doubles the padding. */}
            <main id="content" className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:py-8">
              {children}
            </main>
          </ToastProvider>
        </AuthProvider>
      </body>
    </html>
  );
}
