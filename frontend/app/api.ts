// La API vive en otro origen (puerto 8000); el navegador la llama directo.
export const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type Asiento = {
  estado: "retenida" | "confirmada" | null;
  usuario_id: string | null;
  vence_en: string | null;
};

export type Estado = {
  capacidad: number;
  total: number;
  ahora: string;
  asientos: Record<string, Asiento>;
};

export async function post<T>(ruta: string, cuerpo: unknown): Promise<T> {
  const r = await fetch(`${API}${ruta}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(cuerpo),
  });
  if (!r.ok) throw new Error(`${ruta}: HTTP ${r.status} ${await r.text()}`);
  return r.json() as Promise<T>;
}

export const nuevaClave = () => crypto.randomUUID();

/**
 * Abre el stream de un evento y mantiene `estado` al día.
 * `desfase` = hora de la base de datos - hora del navegador, en ms: todo temporizador se
 * calcula con él, nunca con el reloj del navegador a secas (DESIGN.md §7.1).
 */
export function suscribir(
  eventoId: number,
  alCambiar: (e: Estado, desfase: number) => void,
): () => void {
  let estado: Estado | null = null;
  let desfase = 0;
  const fuente = new EventSource(`${API}/eventos/${eventoId}/stream`);
  const reloj = (ahora: string) => (desfase = Date.parse(ahora) - Date.now());

  fuente.addEventListener("estado", (m) => {
    estado = JSON.parse((m as MessageEvent).data) as Estado;
    reloj(estado.ahora);
    alCambiar(estado, desfase);
  });
  fuente.addEventListener("asiento", (m) => {
    if (!estado) return;
    const d = JSON.parse((m as MessageEvent).data);
    reloj(d.ahora);
    const asientos = { ...estado.asientos };
    if (d.estado === null) delete asientos[d.asiento_id];
    else
      asientos[d.asiento_id] = {
        estado: d.estado,
        usuario_id: d.usuario_id,
        vence_en: d.vence_en,
      };
    estado = { ...estado, asientos };
    alCambiar(estado, desfase);
  });
  fuente.addEventListener("resumen", (m) => {
    if (!estado) return;
    const d = JSON.parse((m as MessageEvent).data);
    reloj(d.ahora);
    estado = { ...estado, total: d.total };
    alCambiar(estado, desfase);
  });
  return () => fuente.close();
}
