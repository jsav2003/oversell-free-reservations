"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Estado, nuevaClave, post, suscribir } from "../api";

type Mia = { reservaId: number; venceEn: string };
type Retencion = { estado: string; reserva_id: number | null; vence_en: string | null };
type Confirmacion = { estado: string };

const dormir = (ms: number) => new Promise((r) => setTimeout(r, ms));

export default function Mapa() {
  const [eventoId, setEventoId] = useState<number | null>(null);
  const [estado, setEstado] = useState<Estado | null>(null);
  const desfase = useRef(0);
  const [usuario] = useState(() => `u${Math.random().toString(36).slice(2, 7)}`);
  const [pendientes, setPendientes] = useState<Set<number>>(new Set());
  const [mias, setMias] = useState<Record<number, Mia>>({});
  const [aviso, setAviso] = useState<{ texto: string; error: boolean } | null>(null);
  const [ttl, setTtl] = useState(30);
  const [demoraPago, setDemoraPago] = useState(2);
  const [reloj, setReloj] = useState(0);
  const pagando = useRef(new Set<number>());

  // El evento puede venir en la URL (?evento=12) para que dos pestañas compitan.
  useEffect(() => {
    const id = new URLSearchParams(window.location.search).get("evento");
    if (id) setEventoId(Number(id));
  }, []);

  useEffect(() => {
    if (eventoId === null) return;
    return suscribir(eventoId, (e, d) => {
      desfase.current = d;
      setEstado(e);
    });
  }, [eventoId]);

  // Repinta el temporizador; el tiempo restante sale de la hora de la base, no de la local.
  useEffect(() => {
    const t = setInterval(() => setReloj((n) => n + 1), 250);
    return () => clearInterval(t);
  }, []);

  const ahoraServidor = () => Date.now() + desfase.current;

  // Una retención que vence sin pagarse deja de ser tuya: se quita su fila y su botón "Pagar".
  // Las que están pagándose las resuelve la respuesta del servidor, no el reloj de aquí.
  useEffect(() => {
    const vencidas = Object.entries(mias)
      .filter(([k, m]) => !pagando.current.has(Number(k)) && Date.parse(m.venceEn) <= ahoraServidor())
      .map(([k]) => Number(k));
    if (vencidas.length === 0) return;
    setMias((x) => {
      const copia = { ...x };
      vencidas.forEach((n) => delete copia[n]);
      return copia;
    });
    setAviso({
      texto: `Tu retención del asiento ${vencidas.join(", ")} venció sin pagarse y volvió a estar disponible.`,
      error: true,
    });
  }, [reloj, mias]);

  async function crearEvento() {
    const r = await post<{ evento_id: number }>("/eventos", { nombre: "mapa", capacidad: 40 });
    window.history.replaceState(null, "", `/mapa?evento=${r.evento_id}`);
    setEventoId(r.evento_id);
  }

  const quitarPendiente = (n: number) =>
    setPendientes((p) => {
      const s = new Set(p);
      s.delete(n);
      return s;
    });

  async function elegir(n: number) {
    if (eventoId === null) return;
    setAviso(null);
    setPendientes((p) => new Set(p).add(n)); // UI optimista: el asiento ya aparece como tuyo
    try {
      const r = await post<Retencion>("/retenciones", {
        evento_id: eventoId,
        usuario_id: usuario,
        clave_idempotencia: nuevaClave(),
        asiento_id: n,
        ttl_segundos: ttl,
      });
      if (r.estado === "retenida" && r.reserva_id !== null && r.vence_en) {
        setMias((m) => ({ ...m, [n]: { reservaId: r.reserva_id!, venceEn: r.vence_en! } }));
      } else if (r.estado === "ocupado") {
        setAviso({
          texto: `El asiento ${n} ya estaba tomado por otra persona un instante antes que tú. Revertimos tu selección.`,
          error: true,
        });
      } else {
        setAviso({ texto: `No se pudo retener el asiento ${n}: ${r.estado}.`, error: true });
      }
    } catch {
      setAviso({ texto: `No se pudo contactar al servidor; el asiento ${n} no quedó tuyo.`, error: true });
    } finally {
      quitarPendiente(n);
    }
  }

  const pagar = useCallback(
    async (n: number) => {
      const m = mias[n];
      if (!m) return;
      pagando.current.add(n);
      setAviso({ texto: `Procesando el pago del asiento ${n}…`, error: false });
      await dormir(demoraPago * 1000); // pago simulado (NO-GOALS: sin pagos reales)
      try {
        const c = await post<Confirmacion>(`/reservas/${m.reservaId}/confirmar`, {
          usuario_id: usuario,
        });
        if (c.estado === "exitosa") {
          setAviso({ texto: `¡Asiento ${n} confirmado!`, error: false });
        } else if (c.estado === "vencida") {
          setAviso({
            texto: `Tu retención del asiento ${n} venció mientras el pago estaba en curso. No se confirmó y el asiento volvió a estar disponible.`,
            error: true,
          });
        } else {
          setAviso({
            texto: `El asiento ${n} ya no era tuyo cuando llegó el pago (${c.estado}). No se confirmó.`,
            error: true,
          });
        }
      } catch {
        setAviso({ texto: "No se pudo contactar al servidor para confirmar el pago.", error: true });
      }
      pagando.current.delete(n);
      setMias((x) => {
        const copia = { ...x };
        delete copia[n];
        return copia;
      });
    },
    [mias, usuario, demoraPago],
  );

  function claseDe(n: number): { clase: string; texto?: string } {
    const a = estado?.asientos[String(n)];
    const venceEn = a?.vence_en ? Date.parse(a.vence_en) : null;
    const vencida = a?.estado === "retenida" && venceEn !== null && venceEn <= ahoraServidor();
    if (!a || vencida) return pendientes.has(n) ? { clase: "pendiente" } : { clase: "" };
    const esMio = a.usuario_id === usuario;
    if (a.estado === "confirmada") return { clase: esMio ? "vendido" : "otro" };
    const restante = Math.max(0, Math.ceil(((venceEn ?? 0) - ahoraServidor()) / 1000));
    return esMio ? { clase: "mio", texto: `${restante}s` } : { clase: "retenido", texto: `${restante}s` };
  }

  if (eventoId === null) {
    return (
      <>
        <h1>Mapa de asientos</h1>
        <p className="suave">Abre el mismo evento en dos pestañas y compite contigo mismo.</p>
        <button className="primario" onClick={crearEvento}>Crear evento de 40 asientos</button>
      </>
    );
  }

  const asientos = Array.from({ length: estado?.capacidad ?? 40 }, (_, i) => i + 1);
  return (
    <>
      <h1>Mapa de asientos</h1>
      <p className="suave">
        Evento {eventoId} · eres <b>{usuario}</b> · comparte{" "}
        <code>/mapa?evento={eventoId}</code> para competir desde otra pestaña.
      </p>
      <div className="fila">
        <label>
          Retención (s)
          <input type="number" min={1} value={ttl} onChange={(e) => setTtl(Number(e.target.value))} />
        </label>
        <label>
          Demora del pago (s)
          <input type="number" min={0} value={demoraPago} onChange={(e) => setDemoraPago(Number(e.target.value))} />
        </label>
      </div>
      <div className="leyenda">
        <span>Libre</span>
        <span style={{ ["--c" as string]: "var(--mio)" }}>Tuyo (retenido)</span>
        <span style={{ ["--c" as string]: "var(--retenido)" }}>Retenido por otro</span>
        <span style={{ ["--c" as string]: "var(--vendido)" }}>Tuyo (comprado)</span>
        <span style={{ ["--c" as string]: "var(--otro)" }}>Vendido</span>
      </div>
      <div className="mapa">
        {asientos.map((n) => {
          const { clase, texto } = claseDe(n);
          const libre = clase === "";
          return (
            <button key={n} className={`asiento ${clase}`} disabled={!libre} onClick={() => elegir(n)}>
              {n}
              {texto && <small>{texto}</small>}
            </button>
          );
        })}
      </div>
      {aviso && <div className={`aviso${aviso.error ? " error" : ""}`}>{aviso.texto}</div>}
      {Object.keys(mias).map((k) => (
        <div className="fila" key={k}>
          <span>Asiento {k} retenido a tu nombre.</span>
          <button className="primario" onClick={() => pagar(Number(k))}>Pagar</button>
        </div>
      ))}
    </>
  );
}
