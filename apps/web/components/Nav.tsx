"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const links = [
  ["/", "Overview"],
  ["/analysis", "Analysis"],
  ["/data", "Data"],
  ["/learning", "Learning"],
  ["/forward", "Forward"],
  ["/sync", "Sync"],
  ["/research", "Research"],
  ["/memory", "Memory"],
  ["/performance", "Performance"],
  ["/settings", "Settings"],
];

export function Nav() {
  const pathname = usePathname();
  return (
    <aside className="sidebar">
      <div className="logo-block">
        <div className="logo-mark">FW</div>
        <div><strong>ForexWizard</strong><span>AI Market Brain</span></div>
      </div>
      <nav>
        {links.map(([href, label]) => (
          <Link key={href} href={href} className={pathname === href ? "nav-link active" : "nav-link"}>
            <span className="nav-dot" />{label}
          </Link>
        ))}
      </nav>
      <div className="sidebar-note">
        <strong>Experimental analysis</strong>
        <span>Evidence-based market research, not guaranteed trading outcomes.</span>
      </div>
    </aside>
  );
}
