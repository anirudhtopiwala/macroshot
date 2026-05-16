import InstallPwaSection from '../components/InstallPwaSection';
import BackgroundOrbs from './about/BackgroundOrbs';
import Header from './about/Header';
import Footer from './about/Footer';
import Hero from './about/Hero';
import FeatureGrid from './about/FeatureGrid';
import DeepDives from './about/DeepDives';
import SelfHost from './about/SelfHost';
import BetaCTA from './about/BetaCTA';
import Privacy from './about/Privacy';
import Community from './about/Community';
import FinalCTA from './about/FinalCTA';

export default function About() {
  return (
    <div
      className="relative min-h-screen overflow-x-clip"
      style={{ background: 'var(--bg-base)' }}
    >
      <BackgroundOrbs />
      <Header />
      <main className="max-w-5xl mx-auto px-4 md:px-6">
        <Hero />
        <FeatureGrid />
        <DeepDives />
        <SelfHost />
        <BetaCTA />
        <Privacy />
        <InstallPwaSection />
        <Community />
        <FinalCTA />
      </main>
      <Footer />
    </div>
  );
}
