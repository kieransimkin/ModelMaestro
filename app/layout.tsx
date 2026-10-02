import type {Metadata} from 'next';
import './globals.css';
export const metadata: Metadata = {title: 'ModelMaestro', description: 'DanceFlow’s local model workbench. Your models, tools and tasks—in sync.'};
export default function RootLayout({children}: Readonly<{children: React.ReactNode}>) {
  return <html lang="en"><body>{children}</body></html>;
}
