"""İTÜ bağıl değerlendirme (T-skor) harf notu tahmini.

İTÜ'nün yönetmelikte tanımladığı iki yöntemi uygular:

- Yöntem 1: sınıf ortalaması/standart sapmasından T-skoru hesaplanır, T-skoru
  sınıfın "düzeyine" (Tablo 1) göre harf notuna çevrilir.
- Yöntem 2: harf notu sınırları doğrudan sınıf ortalaması ± standart sapmanın
  katları olarak hesaplanır (Tablo 2) — arada bir bakım tablosuna gerek yok.

Tablo 1, yönetmelikte açıkça "(Örnektir)" diye işaretli — gerçek T-skor
sınırlarını ders bazında öğretim üyesi belirler. Bu modül o örnek tabloyu
varsayılan olarak kullanır ama her sonucun içine bunun bir tahmin olduğunu
açıkça yazar; resmi harf notu yerine geçmez.
"""

from __future__ import annotations

import math
from typing import Any

# Harf notu sırası, en düşükten en yükseğe. Her iki tabloda da bu sıra ve bu
# 13 not (FF hariç, o iki yöntemde de "hiçbirinin eşiğini tutturamadın" ucu).
GRADE_ORDER: list[str] = [
    "DD", "DD+", "DC", "DC+", "CC", "CC+",
    "CB", "CB+", "BB", "BB+", "BA", "BA+", "AA",
]

# Tablo 1 (Değişik: ÜS-28.12.2023-852) — T-skoru alt sınırları, sınıf ortalaması
# bandına göre. "Mükemmel" satırındaki BA değeri (54) görüntüdeki tabloda
# eksikti; diğer yedi satırın tamamında istisnasız görülen +2/+3 alternating
# artış deseninden (BB+ 51 ile BA+ 56 arasına) geri hesaplandı.
TABLE_1: list[dict[str, Any]] = [
    {
        # Belge "80,00 ≤ x̄ < 100" diyor (100.00 tam olarak hiçbir banda girmez,
        # tabloyu hazırlayanın gözden kaçırdığı bir uç durum); 100.01 üst sınırı
        # ortalaması tam 100 olan bir sınıfı da makul şekilde bu bantta tutar.
        "min_avg": 80.00, "max_avg": 100.01, "label": "Üstün başarı",
        "thresholds": dict(zip(GRADE_ORDER, [27, 29, 32, 34, 37, 39, 42, 44, 47, 49, 52, 54, 57])),
    },
    {
        "min_avg": 70.00, "max_avg": 80.00, "label": "Mükemmel",
        "thresholds": dict(zip(GRADE_ORDER, [29, 31, 34, 36, 39, 41, 44, 46, 49, 51, 54, 56, 59])),
    },
    {
        "min_avg": 62.50, "max_avg": 70.00, "label": "Pekiyi",
        "thresholds": dict(zip(GRADE_ORDER, [31, 33, 36, 38, 41, 43, 46, 48, 51, 53, 56, 58, 61])),
    },
    {
        "min_avg": 57.50, "max_avg": 62.50, "label": "İyi",
        "thresholds": dict(zip(GRADE_ORDER, [33, 35, 38, 40, 43, 45, 48, 50, 53, 55, 58, 60, 63])),
    },
    {
        "min_avg": 52.50, "max_avg": 57.50, "label": "Ortanın Üstü",
        "thresholds": dict(zip(GRADE_ORDER, [35, 37, 40, 42, 45, 47, 50, 52, 55, 57, 60, 62, 65])),
    },
    {
        "min_avg": 47.50, "max_avg": 52.50, "label": "Orta",
        "thresholds": dict(zip(GRADE_ORDER, [37, 39, 42, 44, 47, 49, 52, 54, 57, 59, 62, 64, 67])),
    },
    {
        "min_avg": 42.50, "max_avg": 47.50, "label": "Zayıf",
        "thresholds": dict(zip(GRADE_ORDER, [39, 41, 44, 46, 49, 51, 54, 56, 59, 61, 64, 66, 69])),
    },
    {
        "min_avg": float("-inf"), "max_avg": 42.50, "label": "Kötü",
        "thresholds": dict(zip(GRADE_ORDER, [41, 43, 46, 48, 51, 53, 56, 58, 61, 63, 66, 68, 71])),
    },
]

# Tablo 2 — harf notu alt sınırı = sınıf ortalaması + katsayı × standart sapma.
TABLE_2_COEFFICIENTS: dict[str, float] = {
    "AA": 2.00, "BA+": 1.75, "BA": 1.50, "BB+": 1.25, "BB": 1.00,
    "CB+": 0.75, "CB": 0.50, "CC+": 0.25, "CC": 0.00,
    "DC+": -0.25, "DC": -0.50, "DD+": -0.75, "DD": -1.00,
}


def class_average(scores: list[float]) -> float:
    """Sınıf ortalaması x̄ = Σx/N, virgülden sonra iki haneye yuvarlanır."""
    if not scores:
        raise ValueError("scores boş olamaz.")
    return round(sum(scores) / len(scores), 2)


def standard_deviation(scores: list[float]) -> float:
    """STD = (1/N)·√(N·Σx² − (Σx)²), virgülden sonra iki haneye yuvarlanır.

    Bu formül popülasyon standart sapmasıyla matematiksel olarak eşdeğerdir
    (N·Σx²−(Σx)² = N²·σ²), ama belgedeki ifadeyle birebir eşleşsin diye
    ``statistics.pstdev`` yerine doğrudan yazılmıştır.
    """
    if not scores:
        raise ValueError("scores boş olamaz.")
    n = len(scores)
    total = sum(scores)
    total_sq = sum(x * x for x in scores)
    radicand = n * total_sq - total * total
    # Yuvarlama artıklarından kaynaklanan ufak negatif değerleri 0'a kelepçele.
    radicand = max(0.0, radicand)
    return round((1.0 / n) * math.sqrt(radicand), 2)


def t_score(x: float, mean: float, std: float) -> float:
    """T = 10·((x−x̄)/STD) + 50, virgülden sonra iki haneye yuvarlanır."""
    if std == 0:
        raise ValueError("Standart sapma 0 olduğunda T-skoru tanımsızdır (tüm puanlar eşit).")
    return round(10.0 * ((x - mean) / std) + 50.0, 2)


def pick_class_level(mean: float) -> dict[str, Any]:
    """Sınıf ortalamasına uyan Tablo 1 satırını döndürür."""
    for row in TABLE_1:
        if row["min_avg"] <= mean < row["max_avg"]:
            return row
    # Kuramsal olarak erişilemez (aralıklar -inf..100.01'i kapsıyor) ama
    # bir sınırlayıcı olarak en düşük bandı döndür.
    return TABLE_1[-1]


def _highest_grade_met(score_or_t: float, thresholds: dict[str, float]) -> str:
    """Eşiklerden en yükseği score_or_t'yi geçmeyen harfi bulur; hiçbiri geçmezse FF."""
    met = "FF"
    for grade in GRADE_ORDER:
        if score_or_t >= thresholds[grade]:
            met = grade
        else:
            break
    return met


def estimate_method1(t: float, mean: float) -> dict[str, Any]:
    """T-skoru ve sınıf ortalamasından Yöntem 1 (Tablo 1) harf notu tahmini."""
    row = pick_class_level(mean)
    grade = _highest_grade_met(t, row["thresholds"])
    return {
        "sinif_duzeyi": row["label"],
        "harf_notu": grade,
        "kullanilan_esikler": dict(row["thresholds"]),
    }


def estimate_method2(score: float, mean: float, std: float) -> dict[str, Any]:
    """Ham puan, sınıf ortalaması ve standart sapmadan Yöntem 2 (Tablo 2) tahmini."""
    bounds = {grade: round(mean + coeff * std, 2) for grade, coeff in TABLE_2_COEFFICIENTS.items()}
    grade = "FF"
    for candidate in GRADE_ORDER:
        if score >= bounds[candidate]:
            grade = candidate
        else:
            break
    return {"harf_notu": grade, "sinir_degerleri": bounds}


def estimate_relative_grade(class_scores: list[float], my_score: float) -> dict[str, Any]:
    """Sınıf puanlarından ve kendi puanından iki yönteme göre harf notu tahmini.

    ``my_score`` ``class_scores`` içinde yoksa otomatik eklenir (x̄/STD hesabına
    dahil olması gerekir) ve bu ``note``'ta belirtilir. VF alan öğrenciler bu
    hesaba hiç girmemeli — çağıran, ``class_scores``'a yalnızca bağıl
    değerlendirmeye katılan öğrencilerin puanlarını koymalıdır; bu, yalnızca
    puanlardan tespit edilemeyen bir girdi kısıtıdır.
    """
    scores = list(class_scores)
    auto_added = my_score not in scores
    if auto_added:
        scores.append(my_score)

    mean = class_average(scores)
    std = standard_deviation(scores)
    method2 = estimate_method2(my_score, mean, std)

    result: dict[str, Any] = {
        "n": len(scores),
        "class_average": mean,
        "std_dev": std,
        "my_score": my_score,
        "yontem_2": method2,
        "note": (
            "Bu tamamen bilgi amaçlı bir tahmindir. Resmi harf notunu öğretim üyesi "
            "belirler; ek sınav, sınırsız sınav hakkı gibi yönetmelik istisnaları burada "
            "hesaba katılmamıştır."
        ),
    }

    if std > 0:
        t = t_score(my_score, mean, std)
        method1 = estimate_method1(t, mean)
        result["my_t_score"] = t
        result["yontem_1"] = {
            **method1,
            "kaynak_tablo": (
                "Tablo 1 (Değişik: ÜS-28.12.2023-852) — yönetmelikte örnek olarak "
                "verilmiştir; dersin öğretim üyesi farklı T-skor sınırları belirleyebilir."
            ),
        }
    else:
        result["yontem_1"] = None
        result["yontem_1_uyari"] = (
            "Standart sapma 0 (tüm puanlar eşit); T-skoru tanımsız olduğu için "
            "Yöntem 1 hesaplanamadı."
        )

    if auto_added:
        result["auto_added_my_score"] = True
        result["note"] += (
            " my_score, class_scores listesinde bulunamadığı için listeye otomatik "
            "eklendi (sınıf ortalaması/standart sapma hesabına dahil edilmesi gerekir)."
        )

    return result
