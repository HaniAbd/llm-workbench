"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";

const LINKS = [
  { href: "/", label: "Chat" },
  { href: "/classify", label: "Classify" },
  { href: "/ask", label: "Ask the docs" },
];

export default function Nav() {
  const pathname = usePathname();
  return (
    <nav className="sticky top-0 z-30 border-b border-border/70 bg-background/80 backdrop-blur-md">
      <div className="mx-auto flex w-full max-w-3xl items-center gap-1 px-5 py-3">
        <span className="mr-3 font-mono text-xs tracking-tight text-muted-foreground">
          llm<span className="text-primary">/</span>workbench
        </span>
        {LINKS.map((link) => {
          const active = pathname === link.href;
          return (
            <Link
              key={link.href}
              href={link.href}
              aria-current={active ? "page" : undefined}
              className={cn(
                "relative rounded-md px-2.5 py-1.5 text-sm transition-colors",
                active
                  ? "text-foreground"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {link.label}
              {/* The underline moves rather than blinks, so the eye follows
                  which page it landed on. */}
              {active && (
                <span className="absolute inset-x-2.5 -bottom-[13px] h-px bg-primary" />
              )}
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
