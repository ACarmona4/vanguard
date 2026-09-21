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
    throw new Error("Sin conexión con la API. Verifica que esté en ejecución.");
  }
  if (!response.ok)
    throw new Error(
      "El inventario no está disponible. Revisa la API y la conexión a PostgreSQL.",
    );
  return response.json();
}
