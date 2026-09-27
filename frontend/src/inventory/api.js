export async function getInventory(path, filters, signal) {
  const params = new URLSearchParams(
    Object.entries(filters).filter(
      ([, value]) => value !== "" && value != null,
    ),
  );
  let response;
  try {
    response = await fetch(`/api/${path}?${params}`, { signal });
  } catch (error) {
    if (error.name === "AbortError") throw error;
    throw new Error("Unable to connect to the API. Make sure it is running.");
  }
  if (!response.ok)
    throw new Error(
      "The inventory is unavailable. Check the API and PostgreSQL connection.",
    );
  return response.json();
}
