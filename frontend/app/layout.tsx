import type { ReactNode } from "react";
import "./globals.css";

export const metadata = { title: "Reservas sin sobreventa" };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="es">
      <body>
        <main>
          <nav>
            <a href="/">Inicio</a>
            <a href="/mapa">Mapa de asientos</a>
            <a href="/laboratorio">Laboratorio</a>
          </nav>
          {children}
        </main>
      </body>
    </html>
  );
}
