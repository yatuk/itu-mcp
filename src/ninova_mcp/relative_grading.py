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


# Bu sayının altındaki bir sınıfta ortalama ve standart sapma birkaç puana
# bağlıdır; T-skoru ve ortalama ± k·STD sınırları anlamlı bir harf vermez.
# Yönetmelik metni bu modülde yer almadığı için eşik yönetmelikten alıntı
# değildir: aracın kendi alt sınırıdır ve sonuçta böyle belirtilir.
MIN_CLASS_SIZE = 10


def _validated_score(value: Any, label: str) -> float:
    """Puanın sonlu ve 0-100 aralığında bir sayı olduğunu doğrular."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} bir sayı olmalı; gelen değer: {value!r}.")
    if not math.isfinite(value):
        raise ValueError(f"{label} sonlu bir sayı olmalı; gelen değer: {value!r}.")
    if not 0 <= value <= 100:
        raise ValueError(f"{label} 0-100 aralığında olmalı; gelen değer: {value!r}.")
    return float(value)


def estimate_relative_grade(
    class_scores: list[float],
    my_score: float,
    my_score_included: bool | None = None,
) -> dict[str, Any]:
    """Sınıf puanlarından ve kendi puanından iki yönteme göre harf notu tahmini.

    Bütün puanlar sonlu ve 0-100 aralığında olmalıdır; aksi hâlde hangi değerin
    hatalı olduğunu söyleyen bir ``ValueError`` yükselir.

    ``my_score_included`` kendi puanının ``class_scores`` içinde olup olmadığını
    söyler:

    - ``True``: listede zaten var, tekrar eklenmez (listede yoksa hata).
    - ``False``: listede yok, eklenir. Aynı puanı alan bir sınıf arkadaşı
      listede olsa bile eklenir.
    - ``None`` (varsayılan): puan listede geçiyorsa dahil sayılır, geçmiyorsa
      eklenir. Aynı puanı alan başka biri varsa N bir eksik kalır; sonuç bu
      varsayımı ``my_score_included_assumed`` ile bildirir.

    Harf tahmini yalnızca dağılım bunu taşıyorsa verilir: öğrenci sayısı
    ``MIN_CLASS_SIZE`` altındaysa ya da standart sapma 0 ise ``yontem_1`` ve
    ``yontem_2`` ``None`` olur, ``estimate_available`` ``False`` olur ve neden
    ``no_estimate_reason`` ile açıklanır. Betimsel istatistikler her durumda
    döner.

    VF alan öğrenciler bu hesaba hiç girmemeli: çağıran, ``class_scores``'a
    yalnızca bağıl değerlendirmeye katılan öğrencilerin puanlarını koymalıdır;
    bu, yalnızca puanlardan tespit edilemeyen bir girdi kısıtıdır.
    """
    if not isinstance(class_scores, (list, tuple)):
        raise ValueError("class_scores bir puan listesi olmalı.")
    scores = [
        _validated_score(value, f"class_scores[{index}]")
        for index, value in enumerate(class_scores)
    ]
    mine = _validated_score(my_score, "my_score")
    if my_score_included is not None and not isinstance(my_score_included, bool):
        raise ValueError("my_score_included true, false ya da boş olmalı.")

    present = mine in scores
    if my_score_included is True and not present:
        raise ValueError(
            f"my_score_included=true verildi ama {my_score!r} puanı class_scores içinde yok. "
            "Puanı listeye ekleyin ya da my_score_included=false verin."
        )
    included = present if my_score_included is None else my_score_included
    if not included:
        scores.append(mine)

    mean = class_average(scores)
    std = standard_deviation(scores)

    result: dict[str, Any] = {
        "n": len(scores),
        "class_average": mean,
        "std_dev": std,
        "class_min": min(scores),
        "class_max": max(scores),
        "my_score": my_score,
        "my_score_included": included,
        "estimate_available": True,
        "min_class_size": MIN_CLASS_SIZE,
        "note": (
            "Bu tamamen bilgi amaçlı bir tahmindir. Resmi harf notunu öğretim üyesi "
            "belirler; ek sınav, sınırsız sınav hakkı gibi yönetmelik istisnaları burada "
            "hesaba katılmamıştır."
        ),
    }

    reason: str | None = None
    if len(scores) < MIN_CLASS_SIZE:
        reason = "class_too_small"
        message = (
            f"Hesaba giren öğrenci sayısı {len(scores)}; istatistiksel bir harf tahmini için en az "
            f"{MIN_CLASS_SIZE} puan gerekir. Bu eşik aracın kendi alt sınırıdır, yönetmelikten "
            "alıntı değildir. Küçük sınıflarda harf notunu öğretim üyesi doğrudan belirler."
        )
    elif std == 0:
        reason = "zero_std_dev"
        message = (
            "Standart sapma 0 (bütün puanlar eşit). T-skoru tanımsızdır ve ortalama ± k·STD "
            "sınırlarının hepsi ortalamaya eşit olur; bu dağılımdan harf notu çıkarılamaz."
        )

    if reason is None:
        t = t_score(mine, mean, std)
        result["my_t_score"] = t
        result["yontem_1"] = {
            **estimate_method1(t, mean),
            "kaynak_tablo": (
                "Tablo 1 (Değişik: ÜS-28.12.2023-852): yönetmelikte örnek olarak "
                "verilmiştir; dersin öğretim üyesi farklı T-skor sınırları belirleyebilir."
            ),
        }
        result["yontem_2"] = estimate_method2(mine, mean, std)
    else:
        result["estimate_available"] = False
        result["no_estimate_reason"] = reason
        result["no_estimate_message"] = message
        result["yontem_1"] = None
        result["yontem_2"] = None
        if reason == "zero_std_dev":
            result["yontem_1_uyari"] = message

    if not included:
        result["auto_added_my_score"] = True
        result["note"] += (
            " my_score, class_scores listesine eklendi (sınıf ortalaması/standart sapma "
            "hesabına dahil edilmesi gerekir)."
            if my_score_included is False else
            " my_score, class_scores listesinde bulunamadığı için listeye otomatik "
            "eklendi (sınıf ortalaması/standart sapma hesabına dahil edilmesi gerekir)."
        )
    elif my_score_included is None:
        result["my_score_included_assumed"] = True
        result["note"] += (
            " my_score değeri class_scores içinde geçtiği için listede sayıldı. Bu puan aynı "
            "notu alan başka bir öğrenciye aitse my_score_included=false verin; aksi hâlde N "
            "bir eksik kalır."
        )

    return result
