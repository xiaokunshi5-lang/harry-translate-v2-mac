import type { ReactNode } from "react";

export function SettingsSection({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: ReactNode;
}) {
  return (
    <section className="settings-section">
      <header className="settings-section__header">
        <h2>{title}</h2>
        {description ? <p>{description}</p> : null}
      </header>
      <div className="settings-section__body">{children}</div>
    </section>
  );
}

export function SettingsField({
  label,
  help,
  children,
  htmlFor,
}: {
  label: string;
  help?: string;
  children: ReactNode;
  htmlFor?: string;
}) {
  return (
    <div className="settings-field">
      <span className="settings-field__copy">
        <label htmlFor={htmlFor}>
          <strong>{label}</strong>
        </label>
        {help ? <small>{help}</small> : null}
      </span>
      <span className="settings-field__control">{children}</span>
    </div>
  );
}

export function Select({
  id,
  value,
  onChange,
  children,
  disabled,
  ariaLabel,
}: {
  id?: string;
  value: string | number;
  onChange: (value: string) => void;
  children: ReactNode;
  disabled?: boolean;
  ariaLabel?: string;
}) {
  return (
    <select
      id={id}
      className="settings-select"
      aria-label={ariaLabel}
      value={value}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value)}
    >
      {children}
    </select>
  );
}

export function Toggle({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      className={`settings-toggle${checked ? " is-on" : ""}`}
      onClick={() => onChange(!checked)}
    >
      <span />
    </button>
  );
}

export function Segmented<T extends string>({
  value,
  options,
  onChange,
  label,
}: {
  value: T;
  options: Array<{ value: T; label: string }>;
  onChange: (value: T) => void;
  label: string;
}) {
  return (
    <div className="settings-segmented" role="group" aria-label={label}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          aria-pressed={value === option.value}
          className={value === option.value ? "is-selected" : ""}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

export function Slider({
  id,
  value,
  min,
  max,
  step,
  suffix,
  onChange,
  ariaLabel,
}: {
  id?: string;
  value: number;
  min: number;
  max: number;
  step: number;
  suffix?: string;
  onChange: (value: number) => void;
  ariaLabel?: string;
}) {
  return (
    <span className="settings-slider">
      <input
        id={id}
        type="range"
        aria-label={ariaLabel}
        value={value}
        min={min}
        max={max}
        step={step}
        onChange={(event) => onChange(Number(event.target.value))}
      />
      <output>
        {Number.isInteger(value) ? value : value.toFixed(2)}
        {suffix}
      </output>
    </span>
  );
}

export function ColorSwatches({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  const colors = ["#FFFFFF", "#4FD1C5", "#FFD54F", "#9AE66E", "#F49AB5"];
  return (
    <span className="settings-swatches">
      {colors.map((color) => (
        <button
          key={color}
          type="button"
          aria-label={color}
          aria-pressed={value.toUpperCase() === color}
          className={value.toUpperCase() === color ? "is-selected" : ""}
          style={{ background: color }}
          onClick={() => onChange(color)}
        />
      ))}
      <label className="settings-swatch-custom" title="自定义颜色">
        <input
          type="color"
          value={value.startsWith("#") ? value : "#ffffff"}
          onChange={(event) => onChange(event.target.value.toUpperCase())}
        />
      </label>
    </span>
  );
}
