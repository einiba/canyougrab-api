import { useState } from "react";

const sizes = {
  sm: "w-6 h-6 text-xs",
  md: "w-8 h-8 text-sm",
  lg: "w-16 h-16 text-xl",
} as const;

interface AvatarProps {
  src?: string;
  name: string;
  size?: keyof typeof sizes;
  className?: string;
}

export function Avatar({ src, name, size = "md", className = "" }: AvatarProps) {
  const [imgFailed, setImgFailed] = useState(false);
  const initial = name.charAt(0).toUpperCase() || "?";

  if (src && !imgFailed) {
    return (
      <img
        src={src}
        alt={name}
        onError={() => setImgFailed(true)}
        className={`${sizes[size]} rounded-full object-cover shrink-0 ${className}`}
        referrerPolicy="no-referrer"
      />
    );
  }

  return (
    <div
      className={`${sizes[size]} rounded-full shrink-0 flex items-center justify-center bg-primary/20 text-primary font-medium ${className}`}
    >
      {initial}
    </div>
  );
}
