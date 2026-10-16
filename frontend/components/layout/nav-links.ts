/** The main navigation, shared by the desktop header and the mobile menu. */
export interface NavLink {
  href: string;
  label: string;
  description: string;
}

export const NAV_LINKS: readonly NavLink[] = [
  { href: "/studio", label: "Founder Studio", description: "Plan a business location and prepare a loan-ready file." },
  { href: "/lender", label: "Lender Desk", description: "Review files with the evidence beside every figure." },
  { href: "/about", label: "About", description: "Data sources, licenses, limits and the team." },
];

/** True when `pathname` is the link's page or one of its sub-pages (works with and without trailing slashes). */
export function isActivePath(pathname: string | null, href: string): boolean {
  if (pathname === null) {
    return false;
  }
  const normalized = pathname.endsWith("/") && pathname.length > 1 ? pathname.slice(0, -1) : pathname;
  return normalized === href || normalized.startsWith(`${href}/`);
}
