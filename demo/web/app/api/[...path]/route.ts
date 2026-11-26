// The page asks its own address for the data, and this hands the question to
// the Python API (demo/api.py). The address of the API is read when the
// question comes, not when the page is built: one image, any deployment.
export const dynamic = "force-dynamic";

const API = () => (process.env.COPERNICUS_EMS_API ?? "http://127.0.0.1:8000").replace(/\/$/, "");

export async function GET(request: Request, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const target = `${API()}/api/${path.map(encodeURIComponent).join("/")}${new URL(request.url).search}`;
  let answer: Response;
  try {
    answer = await fetch(target, { cache: "no-store" });
  } catch {
    return Response.json(
      { error: "the copernicus-ems API does not answer: is demo/api.py running?" },
      { status: 502 },
    );
  }
  const headers = new Headers();
  for (const name of ["content-type", "cache-control"]) {
    const value = answer.headers.get(name);
    if (value) headers.set(name, value);
  }
  return new Response(answer.body, { status: answer.status, headers });
}
