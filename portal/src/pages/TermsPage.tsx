import { useState } from "react";

const TOS_VERSION = "1.0";
const EFFECTIVE_DATE = "March 27, 2026";

interface OperatorSection {
  name: string;
  tlds: string[];
  aupUrl: string;
  restrictions: string;
}

const operators: OperatorSection[] = [
  {
    name: "ICANN (Internet Corporation for Assigned Names and Numbers)",
    tlds: ["All generic TLDs (gTLDs)"],
    aupUrl:
      "https://www.icann.org/resources/pages/whois-policies-provisions-2013-04-15-en",
    restrictions:
      "Registration data must not be used for marketing purposes. Bulk redistribution is prohibited unless incorporated into a value-added product that does not permit extraction of a substantial portion of the underlying data.",
  },
  {
    name: "Verisign",
    tlds: [".com", ".net"],
    aupUrl:
      "https://www.verisign.com/en_US/domain-names/registration-data-access-protocol/terms-service/index.xhtml",
    restrictions:
      "High-volume automated queries are prohibited except when reasonably necessary to register domain names or modify existing registrations. Compilation, repackaging, dissemination, or other use of the data requires prior written consent from Verisign.",
  },
  {
    name: "Public Interest Registry (PIR)",
    tlds: [".org"],
    aupUrl: "https://pir.org/our-domains/whois-look-up/",
    restrictions:
      "Query rate is limited to 10 queries per minute. Access to searchable WHOIS beyond basic lookups requires authorization with a demonstrated legitimate purpose.",
  },
  {
    name: "Identity Digital",
    tlds: [".io", ".co", ".cc", ".tv", "and 300+ new gTLDs"],
    aupUrl: "https://identity.digital",
    restrictions:
      "Data mining and systematic collection of registration data is prohibited. Standard ICANN acceptable use policy applies.",
  },
  {
    name: "Google Registry",
    tlds: [".dev", ".app", ".page", ".new", ".google"],
    aupUrl: "https://about.google/products/",
    restrictions:
      "Standard ICANN restrictions apply. WHOIS is disabled for .app (RDAP only).",
  },
  {
    name: "CentralNic",
    tlds: [".xyz", ".online", ".site", ".store", ".fun"],
    aupUrl: "https://centralnic.com",
    restrictions:
      "Standard ICANN acceptable use restrictions. Rate limiting enforced via HTTP 429 responses.",
  },
  {
    name: "GoDaddy Registry (formerly NeuStar)",
    tlds: [".us", ".biz"],
    aupUrl: "https://registry.godaddy",
    restrictions:
      "High-volume automated electronic processes are prohibited. Unauthenticated access is limited to approximately 20 queries before IP-based blocking. Compilation or redistribution of a substantial portion of the database requires written permission.",
  },
  {
    name: "DENIC",
    tlds: [".de"],
    aupUrl: "https://www.denic.de",
    restrictions:
      "German law applies. Automated queries without an explicit agreement with DENIC are not permitted. Data use is restricted to purposes directly related to domain name registration.",
  },
  {
    name: "Nominet",
    tlds: [".uk", ".co.uk"],
    aupUrl: "https://www.nominet.uk",
    restrictions:
      "UK data protection law applies. Automated bulk access requires a separate agreement with Nominet.",
  },
  {
    name: "CIRA (Canadian Internet Registration Authority)",
    tlds: [".ca"],
    aupUrl: "https://www.cira.ca",
    restrictions:
      "Canadian privacy law applies. WHOIS data use is subject to CIRA's published acceptable use policy.",
  },
  {
    name: "JPRS (Japan Registry Services)",
    tlds: [".jp"],
    aupUrl: "https://jprs.co.jp",
    restrictions:
      "Japanese law applies. Automated queries are subject to JPRS rate limits and acceptable use policy.",
  },
  {
    name: "AFNIC",
    tlds: [".fr"],
    aupUrl: "https://www.afnic.fr",
    restrictions:
      "French and EU data protection law applies. WHOIS data use is subject to AFNIC's published terms.",
  },
  {
    name: "auDA (au Domain Administration)",
    tlds: [".au", ".com.au"],
    aupUrl: "https://www.auda.org.au",
    restrictions:
      "Australian privacy law applies. WHOIS data use is subject to auDA's published acceptable use policy.",
  },
];

function Accordion({
  title,
  children,
  defaultOpen = false,
}: {
  title: string;
  children: React.ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="border border-border rounded-lg overflow-hidden">
      <button
        className="w-full flex items-center justify-between px-4 py-3 text-left text-sm font-medium text-foreground hover:bg-secondary/50 transition-colors"
        onClick={() => setOpen(!open)}
      >
        <span>{title}</span>
        <svg
          className={`w-4 h-4 text-muted-foreground transition-transform ${open ? "rotate-180" : ""}`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M19 9l-7 7-7-7"
          />
        </svg>
      </button>
      {open && <div className="px-4 pb-4 text-sm text-muted-foreground">{children}</div>}
    </div>
  );
}

export function TermsPage() {
  return (
    <div className="max-w-3xl mx-auto space-y-10">
      <div>
        <h1 className="text-2xl font-bold text-foreground">Terms of Service</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Version {TOS_VERSION} &mdash; Effective {EFFECTIVE_DATE}
        </p>
      </div>

      {/* Section 1: Platform Terms */}
      <section className="space-y-4">
        <h2 className="text-lg font-semibold text-foreground">1. Platform Terms</h2>
        <div className="text-sm text-muted-foreground space-y-3 leading-relaxed">
          <p>
            CanYouGrab (&ldquo;the Service&rdquo;) provides a domain name availability
            checking API. By creating an account or using an API key, you agree to these
            Terms of Service.
          </p>

          <h3 className="font-medium text-foreground pt-2">Acceptable Use</h3>
          <ul className="list-disc pl-5 space-y-1">
            <li>
              You may use the Service to check domain name availability for lawful purposes
              related to domain name registration.
            </li>
            <li>
              You must not use the Service to compile, harvest, or build databases of
              domain registration data for resale or redistribution.
            </li>
            <li>
              You must not use data obtained through the Service for unsolicited
              communications, advertising, or marketing of any kind.
            </li>
            <li>
              You must not attempt to circumvent rate limits or access controls imposed by
              the Service or by upstream data providers.
            </li>
          </ul>

          <h3 className="font-medium text-foreground pt-2">Rate Limits &amp; Fair Use</h3>
          <p>
            API usage is subject to the rate limits of your plan. We reserve the right to
            throttle or suspend accounts that generate excessive load, violate these terms,
            or negatively impact service availability for other users.
          </p>

          <h3 className="font-medium text-foreground pt-2">Data Accuracy</h3>
          <p>
            Domain availability results are provided on a best-effort basis. We do not
            guarantee the accuracy or completeness of any data returned by the API. You
            should verify availability with the relevant registrar before completing a
            domain registration.
          </p>

          <h3 className="font-medium text-foreground pt-2">Account Termination</h3>
          <p>
            We may suspend or terminate your account at any time if we reasonably believe
            you have violated these terms or the acceptable use policies of any upstream
            data provider.
          </p>
        </div>
      </section>

      {/* Section 2: Third-Party Data Provider Terms */}
      <section className="space-y-4">
        <h2 className="text-lg font-semibold text-foreground">
          2. Third-Party Data Provider Terms
        </h2>
        <div className="text-sm text-muted-foreground space-y-3 leading-relaxed">
          <p>
            The Service queries RDAP (Registration Data Access Protocol) and WHOIS servers
            operated by domain name registry operators to determine availability. Each
            registry operator publishes their own acceptable use policy governing how their
            data may be queried and used.
          </p>
          <p>
            <strong className="text-foreground">
              By using the Service, you agree to comply with the acceptable use policies of
              each registry operator whose data you access through the Service.
            </strong>{" "}
            The key operators and their restrictions are summarized below. The full terms
            are available at the linked URLs.
          </p>
        </div>

        <div className="space-y-2">
          {operators.map((op) => (
            <Accordion key={op.name} title={op.name}>
              <div className="space-y-2 pt-1">
                <div>
                  <span className="text-foreground font-medium">TLDs: </span>
                  {op.tlds.map((tld, i) => (
                    <span key={tld}>
                      {i > 0 && ", "}
                      <code className="text-primary">{tld}</code>
                    </span>
                  ))}
                </div>
                <p>{op.restrictions}</p>
                <a
                  href={op.aupUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-primary hover:underline inline-flex items-center gap-1"
                >
                  View full acceptable use policy
                  <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"
                    />
                  </svg>
                </a>
              </div>
            </Accordion>
          ))}
        </div>
      </section>

      {/* Section 3: Privacy */}
      <section className="space-y-4">
        <h2 className="text-lg font-semibold text-foreground">3. Privacy</h2>
        <div className="text-sm text-muted-foreground space-y-3 leading-relaxed">
          <p>
            We collect and store the following data in connection with your use of the
            Service:
          </p>
          <ul className="list-disc pl-5 space-y-1">
            <li>
              <strong className="text-foreground">Account information:</strong> Email
              address, name, and authentication provider as provided through Auth0.
            </li>
            <li>
              <strong className="text-foreground">API usage logs:</strong> Domain names
              queried, timestamps, and API key identifiers. Retained for billing, rate
              limiting, and abuse prevention.
            </li>
            <li>
              <strong className="text-foreground">IP addresses:</strong> Logged for rate
              limiting and security purposes.
            </li>
          </ul>
          <p>
            We do not sell or share your personal information with third parties except as
            required by law or as necessary to operate the Service (e.g., payment
            processing through Stripe).
          </p>
        </div>
      </section>

      {/* Section 4: Contact */}
      <section className="space-y-4 pb-12">
        <h2 className="text-lg font-semibold text-foreground">4. Contact</h2>
        <div className="text-sm text-muted-foreground leading-relaxed">
          <p>
            For questions about these terms, contact us at{" "}
            <a
              href="mailto:support@canyougrab.it"
              className="text-primary hover:underline"
            >
              support@canyougrab.it
            </a>
            .
          </p>
        </div>
      </section>
    </div>
  );
}
