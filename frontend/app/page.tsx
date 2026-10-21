export default function Home() {
  return (
    <>
      <h1>Reservas sin sobreventa</h1>
      <p className="suave">
        Qué estrategias de concurrencia en PostgreSQL sobrevenden, cuáles no, por qué y cuánto
        cuesta cada una.
      </p>
      <ul>
        <li>
          <a href="/mapa">Mapa de asientos</a>: compra en vivo con otros compradores a la vez.
          Si el asiento que ves libre ya no lo está, la pantalla revierte y te lo explica.
        </li>
        <li>
          <a href="/laboratorio">Laboratorio</a>: elige estrategia, nivel de aislamiento y
          número de compradores, y mira si aparece una sobreventa.
        </li>
      </ul>
    </>
  );
}
