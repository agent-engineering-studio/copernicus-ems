import Demo from "@/components/Demo";

type Params = Promise<Record<string, string | string[] | undefined>>;

// Read when the page is asked for, not when it is built: /?code=EMSR664 opens on that activation.
export default async function Page({ searchParams }: { searchParams: Params }) {
  const asked = (await searchParams).code;
  const code = (Array.isArray(asked) ? asked[0] : asked)?.trim().toUpperCase() ?? "";
  return <Demo first={/^EMSR\d{1,5}$/.test(code) ? code : null} />;
}
