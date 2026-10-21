"use client";

import { useEffect, useState } from "react";
import { Estado, post, suscribir } from "../api";

const ESTRATEGIAS = [
  ["e1", "E1 · leer y luego insertar"],
  ["e2", "E2 · UPDATE condicional"],
  ["e3", "E3 · bloqueo pesimista (FOR UPDATE)"],
  ["e4", "E4 · restricción UNIQUE (por asiento)"],
  ["e5", "E5 · SERIALIZABLE con reintento"],
];
const NIVELES = [
  ["read_committed", "READ COMMITTED"],
  ["repeatable_read", "REPEATABLE READ"],
  ["serializable", "SERIALIZABLE"],
];

type Resultado = {
  evento_id: number;
  capacidad: number;
  duracion: number;
  respuestas: Record<string, number>;
  reintentos: number;
  throughput: number;
  p50_ms: number;
  p99_ms: number;
  violaciones: {
    reservas: number;
    sobreventa: number;
    asientos_duplicados: number;
    respuestas_infieles: number;
    claves_repetidas: number;
    retenciones: number;
  };
};

export default function Laboratorio() {
  const [estrategia, setEstrategia] = useState("e1");
  const [aislamiento, setAislamiento] = useState("repeatable_read");
  const [compradores, setCompradores] = useState(500);
  const [capacidad, setCapacidad] = useState(100);
  const [semilla, setSemilla] = useState(1);
  const [reintentar, setReintentar] = useState(false);
  const [corriendo, setCorriendo] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [eventoId, setEventoId] = useState<number | null>(null);
  const [vivo, setVivo] = useState<Estado | null>(null);
  const [resultado, setResultado] = useState<Resultado | null>(null);

  useEffect(() => {
    if (eventoId === null) return;
    return suscribir(eventoId, (e) => setVivo(e));
  }, [eventoId]);

  async function lanzar() {
    setCorriendo(true);
    setError(null);
    setResultado(null);
    setVivo(null);
    try {
      const e = await post<{ evento_id: number }>("/eventos", { nombre: "laboratorio", capacidad });
      setEventoId(e.evento_id);
      const r = await post<Resultado>(`/laboratorio/${e.evento_id}/correr`, {
        estrategia,
        aislamiento,
        compradores,
        semilla,
        reintentar_abortadas: reintentar,
      });
      setResultado(r);
    } catch (e) {
      setError(String(e));
    } finally {
      setCorriendo(false);
    }
  }

  // El modelo por capacidad no tiene asientos con identidad: se dibujan tantos cuadros como
  // reservas, y los que pasan de la capacidad son la sobreventa.
  const total = resultado?.violaciones.reservas ?? vivo?.total ?? 0;
  const cap = resultado?.capacidad ?? vivo?.capacidad ?? capacidad;
  const cuadros = Math.min(Math.max(total, cap), 3000);
  const v = resultado?.violaciones;

  return (
    <>
      <h1>Laboratorio</h1>
      <p className="suave">
        Lanza compradores simultáneos contra un evento nuevo. Corre dentro de la API: es una
        demostración; los números rigurosos salen de la matriz (<code>RESULTS.md</code>).
      </p>
      <div className="fila">
        <label>
          Estrategia
          <select value={estrategia} onChange={(e) => setEstrategia(e.target.value)}>
            {ESTRATEGIAS.map(([k, n]) => <option key={k} value={k}>{n}</option>)}
          </select>
        </label>
        <label>
          Aislamiento
          <select value={aislamiento} onChange={(e) => setAislamiento(e.target.value)}>
            {NIVELES.map(([k, n]) => <option key={k} value={k}>{n}</option>)}
          </select>
        </label>
        <label>
          Compradores
          <input type="number" min={1} max={2000} value={compradores} onChange={(e) => setCompradores(Number(e.target.value))} />
        </label>
        <label>
          Capacidad
          <input type="number" min={1} max={5000} value={capacidad} onChange={(e) => setCapacidad(Number(e.target.value))} />
        </label>
        <label>
          Semilla
          <input type="number" value={semilla} onChange={(e) => setSemilla(Number(e.target.value))} />
        </label>
        <label>
          <span>
            <input type="checkbox" checked={reintentar} onChange={(e) => setReintentar(e.target.checked)} />{" "}
            El cliente reintenta los abortos
          </span>
        </label>
        <button className="primario" onClick={lanzar} disabled={corriendo}>
          {corriendo ? "Corriendo…" : "Lanzar experimento"}
        </button>
      </div>

      {error && <div className="aviso error">{error}</div>}

      {eventoId !== null && (
        <>
          <p>
            Evento {eventoId}: <b>{total}</b> reservas de <b>{cap}</b> de capacidad
            {total > cap && <b style={{ color: "var(--error)" }}> · ¡SOBREVENTA de {total - cap}!</b>}
          </p>
          <div className="mapa">
            {Array.from({ length: cuadros }, (_, i) => {
              const ocupado = i < total;
              const sobra = i >= cap && ocupado;
              return <div key={i} className={`asiento ${sobra ? "sobreventa" : ocupado ? "vendido" : ""}`}>{i + 1}</div>;
            })}
          </div>
        </>
      )}

      {resultado && v && (
        <>
          <table>
            <tbody>
              <tr><th>Sobreventa</th><td className={v.sobreventa ? "mal" : ""}>{v.sobreventa} (reservas {v.reservas} / capacidad {resultado.capacidad})</td></tr>
              <tr><th>Asientos duplicados</th><td className={v.asientos_duplicados ? "mal" : ""}>{v.asientos_duplicados}</td></tr>
              <tr><th>Respuestas infieles</th><td className={v.respuestas_infieles ? "mal" : ""}>{v.respuestas_infieles}</td></tr>
              <tr><th>Claves repetidas</th><td className={v.claves_repetidas ? "mal" : ""}>{v.claves_repetidas}</td></tr>
              <tr><th>Retenciones inválidas</th><td className={v.retenciones ? "mal" : ""}>{v.retenciones}</td></tr>
              <tr><th>Respuestas</th><td>{Object.entries(resultado.respuestas).map(([k, n]) => `${k}: ${n}`).join(" · ")}</td></tr>
              <tr><th>Reintentos</th><td>{resultado.reintentos}</td></tr>
              <tr><th>Throughput</th><td>{resultado.throughput.toFixed(1)} compras/s</td></tr>
              <tr><th>Latencia p50 / p99</th><td>{resultado.p50_ms.toFixed(0)} / {resultado.p99_ms.toFixed(0)} ms</td></tr>
              <tr><th>Duración</th><td>{resultado.duracion.toFixed(2)} s</td></tr>
            </tbody>
          </table>
        </>
      )}
    </>
  );
}
