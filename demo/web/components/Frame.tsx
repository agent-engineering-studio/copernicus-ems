"use client";

import { LANGS, useText, type Lang } from "@/lib/i18n";

export const DOCS = "https://agentengineering.it/copernicus-ems/";
const CODE = "https://github.com/agent-engineering-studio/copernicus-ems";

/** The header of both pages: who makes it, the language switch, the name and what it is for. */
export function Header({ lang, onLang }: { lang: Lang; onLang: (lang: Lang) => void }) {
  const t = useText();
  return (
    <>
      <a className="visually-hidden-focusable skip" href="#content">
        {t.skip}
      </a>
      <header className="it-header-wrapper">
        <div className="it-header-slim-wrapper">
          <div className="container-xxl">
            <div className="it-header-slim-wrapper-content">
              <span className="navbar-brand">{t.owner}</span>
              <div className="it-header-slim-right-zone">
                <div className="langs" role="radiogroup" aria-label={t.language}>
                  {LANGS.map((one) => (
                    <button key={one} role="radio" aria-checked={lang === one} onClick={() => onLang(one)}>
                      {one.toUpperCase()}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </div>
        <div className="it-nav-wrapper">
          <div className="it-header-center-wrapper">
            <div className="container-xxl">
              <div className="it-header-center-content-wrapper">
                <div className="it-brand-wrapper">
                  <div className="it-brand-text">
                    <h1 className="it-brand-title">copernicus-ems</h1>
                    <div className="it-brand-tagline">{t.tagline}</div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </header>
    </>
  );
}

/** The footer of both pages: the data, the limit, how it is made, the links (on `/`, also to `/tecnica`). */
export function Footer({ toTech = false }: { toTech?: boolean }) {
  const t = useText();
  return (
    <footer className="it-footer">
      <div className="it-footer-main">
        <div className="container-xxl">
          <div className="row py-4">
            <section className="col-md-4">
              <h2 className="h6">{t.dataTitle}</h2>
              <p>{t.dataText}</p>
            </section>
            <section className="col-md-4">
              <h2 className="h6">{t.limitTitle}</h2>
              <p>{t.limitText}</p>
            </section>
            <section className="col-md-4">
              <h2 className="h6">{t.howTitle}</h2>
              <p>{t.howText}</p>
              {toTech && (
                <p>
                  <a href={`/tecnica?lang=${t.lang}`}>{t.howLink}</a>
                </p>
              )}
            </section>
          </div>
        </div>
      </div>
      <div className="it-footer-small-prints clearfix">
        <div className="container-xxl">
          <ul className="it-footer-small-prints-list list-inline mb-0 d-flex flex-column flex-md-row">
            <li className="list-inline-item">
              <a href={DOCS}>copernicus-ems · {t.docs}</a>
            </li>
            <li className="list-inline-item">
              <a href={CODE}>{t.code}</a>
            </li>
          </ul>
        </div>
      </div>
    </footer>
  );
}
