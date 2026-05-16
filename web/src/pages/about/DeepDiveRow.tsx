import type { ReactNode } from 'react';
import CheckListItem, { type Accent } from './CheckListItem';

interface DeepDiveRowProps {
  eyebrow: string;
  title: ReactNode;
  body: ReactNode;
  bullets: ReactNode[];
  image: {
    src: string;
    alt: string;
    soft?: boolean;
  };
  reversed?: boolean;
  accent: Accent;
}

const SOFT_MASK =
  'radial-gradient(ellipse 85% 90% at center, black 55%, transparent 98%)';

export default function DeepDiveRow({
  eyebrow,
  title,
  body,
  bullets,
  image,
  reversed = false,
  accent,
}: DeepDiveRowProps) {
  const textClasses = reversed ? 'md:order-2' : '';
  const imgClasses = reversed ? 'md:order-1' : '';
  const imgStyle = image.soft
    ? {
        maskImage: SOFT_MASK,
        WebkitMaskImage: SOFT_MASK,
      }
    : undefined;

  return (
    <section className="py-12 md:py-20">
      <div className="grid md:grid-cols-2 gap-10 md:gap-16 items-center">
        <div className={textClasses}>
          <p className="section-heading mb-3">{eyebrow}</p>
          <h2
            className="text-3xl md:text-4xl font-black tracking-tight mb-5"
            style={{ color: 'var(--text-primary)' }}
          >
            {title}
          </h2>
          <p
            className="text-base leading-relaxed mb-6"
            style={{ color: 'var(--text-secondary)' }}
          >
            {body}
          </p>
          <ul className="space-y-3">
            {bullets.map((bullet, i) => (
              <CheckListItem key={i} accent={accent}>
                {bullet}
              </CheckListItem>
            ))}
          </ul>
        </div>
        <div className={imgClasses}>
          <img
            src={image.src}
            alt={image.alt}
            className="w-full h-auto block"
            style={imgStyle}
          />
        </div>
      </div>
    </section>
  );
}
