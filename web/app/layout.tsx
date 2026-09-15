import type { Metadata } from "next";
import Nav from "./components/Nav";
import { RetrievalConfigProvider } from "./components/RetrievalConfig";
import { TraceProvider } from "./components/TraceDrawer";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "llm-workbench",
  description: "Chat, classification and retrieval against a local model, with the trace behind every call.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      // Dark only. The class is what shadcn and Elements key their `dark:`
      // variants off; the tokens themselves live on :root, so there is no
      // second palette to keep in step.
      className={`dark ${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="flex min-h-full flex-col bg-background text-foreground">
        <RetrievalConfigProvider>
          <TraceProvider>
            <Nav />
            {children}
          </TraceProvider>
        </RetrievalConfigProvider>
      </body>
    </html>
  );
}
