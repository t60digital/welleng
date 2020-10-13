import numpy as np
from numpy.linalg import norm

from scipy import optimize
from scipy.spatial.distance import cdist

from welleng.data import Survey, interpolate_survey
from welleng.utils import NEV_to_HLA, HLA_to_NEV

class Clearance:
    def __init__(
        self,
        reference,
        offset,
        k=3.5,
        sigma_pa=0.5,
        Sm=0.3,
        Rr=0.4572,
        Ro=0.3048,
        kop_depth=0,
        method="ISCWSA"
    ):
        """

        """
        self.reference = reference
        self.offset = offset
        self.k = k
        self.sigma_pa = sigma_pa
        self.Sm = Sm
        self.method = method
        
        self._get_kop_index(kop_depth)
        self._get_ref()
        self._get_radii(Rr, Ro)

        self.reference_nevs = self._get_nevs(self.ref)
        self.offset_nevs = self._get_nevs(self.offset)

        # get closest survey station in offset well for each survey 
        # station in the reference well
        self.idx = np.argmin(
            cdist(
                self.reference_nevs, self.offset_nevs
            ), axis=-1
        )

        # iterate to find closest point on offset well between
        # survey stations
        self._get_closest_points()
        self.off_nevs = self._get_nevs(self.off)

        # get the unit vectors between the wells
        self._get_delta_nev_vectors()

        # transform to HLA coordinates
        self._get_delta_hla_vectors()

        # make the covariance matrices
        self._get_covs()

        # get the PCRs
        self._get_PCRs()

        # calculate sigmaS
        self.sigmaS = np.sqrt(self.ref_PCR ** 2 + self.off_PCR ** 2)

        # calculate combined hole radii
        self._get_calc_hole()

        # calculate SF
        self.ISCWSA_ACR = np.around(
            (self.dist_CC_Clr.T - self.calc_hole - self.Sm)
            / (self.k * np.sqrt(self.sigmaS ** 2 + self.sigma_pa ** 2)),
            decimals=2
        ).reshape(-1)

    def _get_calc_hole(self):
        self.calc_hole = self.Rr + self.Ro[self.idx]

    def _get_cov_HLA(self, survey):
        H, L, A = np.array([survey.sigmaH, survey.sigmaL, survey.sigmaA])
        zeros = np.zeros_like(H)
        cov_hla = np.array([
            [H ** 2, zeros, zeros],
            [zeros, L ** 2, zeros],
            [zeros, zeros, A ** 2]
        ]).T

        return cov_hla

    def _get_covs(self):
        self.ref_cov_hla = self._get_cov_HLA(self.ref)
        self.ref_cov_nev = HLA_to_NEV(self.ref.survey_rad, self.ref_cov_hla.T, cov=True).T
        
        self.off_cov_hla = self._get_cov_HLA(self.off)
        self.off_cov_nev = HLA_to_NEV(self.off.survey_rad, self.off_cov_hla.T, cov=True).T

    def _get_PCRs(self):
        self.ref_PCR = np.hstack([
            np.sqrt(np.dot(np.dot(vec, cov), vec.T))
            for vec, cov in zip(self.ref_delta_hlas, self.ref_cov_hla)
            # np.sqrt(np.dot(np.dot(vec.T, cov), vec))
            # for vec, cov in zip(self.ref_delta_nevs, self.ref_cov_nev)
        ])
        self.off_PCR = np.hstack([
            np.sqrt(np.dot(np.dot(vec, cov), vec.T))
            for vec, cov in zip(self.off_delta_hlas, self.off_cov_hla)
            # np.sqrt(np.dot(np.dot(vec.T, cov), vec))
            # for vec, cov in zip(self.off_delta_nevs, self.off_cov_nev)
        ])
        
    def _get_delta_hla_vectors(self):
        self.ref_delta_hlas = np.vstack([
            NEV_to_HLA(s, nev, cov=False)
            for s, nev in zip(
                self.ref.survey_rad, self.ref_delta_nevs
            )
        ])
        self.off_delta_hlas = np.vstack([
            NEV_to_HLA(s, nev, cov=False)
            for s, nev in zip(
                self.off.survey_rad, self.off_delta_nevs
            )
        ])

    def _get_delta_nev_vectors(self):
        temp = self.off_nevs - self.reference_nevs
        self.dist_CC_Clr = norm(temp, axis=-1).reshape(-1,1)
        self.ref_delta_nevs = temp / self.dist_CC_Clr

        temp = self.reference_nevs - self.off_nevs
        self.off_delta_nevs = temp / self.dist_CC_Clr

        self.hoz_bearing = (np.degrees(np.arctan2(
            self.ref_delta_nevs[:,1], self.ref_delta_nevs[:,0]
        )) + 360) % 360

        self.dist_CC_Clr = self.dist_CC_Clr.reshape(-1)

        ###
        # self.ref_delta_nevs = np.array([
        #     self.ref_delta_nevs[:,0],
        #     self.ref_delta_nevs[:,1],
        #     np.zeros_like(self.ref_delta_nevs[:,2])
        # ]).T
        # self.ref_delta_nevs = (
        #     self.ref_delta_nevs
        #     / norm(self.ref_delta_nevs, axis=-1).reshape(-1,1)
        # )
        # self.off_delta_nevs = np.array([
        #     self.off_delta_nevs[:,0],
        #     self.off_delta_nevs[:,1],
        #     np.zeros_like(self.off_delta_nevs[:,2])
        # ]).T
        # self.off_delta_nevs = (
        #     self.off_delta_nevs
        #     / norm(self.off_delta_nevs, axis=-1).reshape(-1,1)
        # )
        ###


    def _get_kop_index(self, kop_depth):
        self.kop_depth = kop_depth
        self.kop_index = np.searchsorted(self.reference.md, self.kop_depth, side="left")

    def _get_nevs(self, survey):
        return np.array([
            survey.n,
            survey.e,
            survey.tvd
        ]).T

    def _fun(self, x, survey, index, station):
        s = interpolate_survey(survey, x[0], index)
        new_pos = np.array([s.n, s.e, s.tvd]).T[1]
        dist = norm(new_pos - station, axis=-1)

        return dist

    def _get_closest_points(self):
        closest = []
        for j, (i, station) in enumerate(zip(self.idx, self.reference_nevs.tolist())):
            # if j < self.kop_index: continue
            if i > 0:
                bnds = [(0, self.offset.md[i] - self.offset.md[i - 1])]
                res_1 = optimize.minimize(
                    self._fun,
                    bnds[0][1],
                    method='SLSQP',
                    bounds=bnds,
                    args=(self.offset, i-1, station)
                    )
                mult = res_1.x[0] / (bnds[0][1] - bnds[0][0])
                sigma_new_1 = self._interpolate_sigmas(i, mult)
            else: res_1 = False

            if i < len(self.offset_nevs) - 1:
                bnds = [(0, self.offset.md[i + 1] - self.offset.md[i])]
                res_2 = optimize.minimize(
                    self._fun,
                    bnds[0][0],
                    method='SLSQP',
                    bounds=bnds,
                    args=(self.offset, i, station)
                    )
                mult = res_2.x[0] / (bnds[0][1] - bnds[0][0])
                sigma_new_2 = self._interpolate_sigmas(i + 1, mult)
            else: res_2 = False

            if res_1 and res_2 and res_1.fun < res_2.fun or not res_2:
                closest.append((station, interpolate_survey(self.offset, res_1.x[0], i - 1), res_1, sigma_new_1))
            else:
                closest.append((station, interpolate_survey(self.offset, res_2.x[0], i), res_2, sigma_new_2))

        self.closest = closest
        md, inc, azi, n, e, tvd, x, y, z, sigmaH, sigmaL, sigmaA = np.array([
            [
                r[1].md[1],
                r[1].inc_rad[1],
                r[1].azi_rad[1],
                r[1].n[1],
                r[1].e[1],
                r[1].tvd[1],
                r[1].x[1],
                r[1].y[1],
                r[1].z[1],
                r[3][0],
                r[3][1],
                r[3][2],
            ]
            for r in self.closest
        ]).T

        self.off = Survey(
            md=md,
            inc=inc,
            azi=azi,
            n=n,
            e=e,
            tvd=tvd,
            sigmaH=sigmaH,
            sigmaL=sigmaL,
            sigmaA=sigmaA,
            start_xyz=[x[0],y[0],z[0]],
            start_nev=[n[0],e[0],tvd[0]],
            deg=False,
            unit=self.ref.unit
        )


    def _interpolate_sigmas(self, i, mult):
        sigmaH_new = (
            self.offset.sigmaH[i - 1]
            + mult * (self.offset.sigmaH[i] - self.offset.sigmaH[i - 1])
        )
        sigmaL_new = (
            self.offset.sigmaL[i - 1]
            + mult * (self.offset.sigmaL[i] - self.offset.sigmaL[i - 1])
        )
        sigmaA_new = (
            self.offset.sigmaA[i - 1]
            + mult * (self.offset.sigmaA[i] - self.offset.sigmaA[i - 1])
        )
        return (sigmaH_new, sigmaL_new, sigmaA_new)

    def _get_radii(self, Rr, Ro):
        """
        If well bore radius data is provided, use this, otherwise use the
        defaults values.
        """
        if self.ref.radius:
            self.Rr = self.ref.radius
        else:
            self.Rr = np.full(len(self.ref.md), Rr)

        if self.offset.radius:
            self.Ro = self.offset.radius
        else:
            self.Ro = np.full(len(self.offset.md), Ro)

    def _get_ref(self):
        if self.kop_index == 0:
            self.ref = self.reference
        else:
            H, L, A = get_ref_sigma(
                self.reference.sigmaH,
                self.reference.sigmaL,
                self.reference.sigmaA,
                self.kop_index
            ).T
            self.ref = Survey(
                md=self.reference.md[self.kop_index:],
                inc=self.reference.inc_rad[self.kop_index:],
                azi=self.reference.azi_rad[self.kop_index:],
                n=self.reference.n[self.kop_index:],
                e=self.reference.e[self.kop_index:],
                tvd=self.reference.tvd[self.kop_index:],
                vec=self.reference.vec,
                radius=self.reference.radius,
                sigmaH=H[self.kop_index:],
                sigmaL=L[self.kop_index:],
                sigmaA=A[self.kop_index:],
                start_xyz=[
                    self.reference.x[self.kop_index],
                    self.reference.y[self.kop_index],
                    self.reference.z[self.kop_index],
                    ],
                start_nev=[
                    self.reference.n[self.kop_index],
                    self.reference.e[self.kop_index],
                    self.reference.tvd[self.kop_index],
                    ],
                deg=self.reference.deg,
                unit=self.reference.unit
            )

    # def _get_sigma_d(self):
        

def get_ref_sigma(sigma1, sigma2, sigma3, kop_index):
    sigma = np.array([sigma1, sigma2, sigma3]).T
    sigma_diff = np.diff(sigma, axis=0)

    sigma_above = np.cumsum(sigma_diff[:kop_index][::-1], axis=0)[::-1]
    sigma_below = np.cumsum(sigma_diff[kop_index:], axis=0)

    sigma_new = np.vstack((sigma_above, np.array([0,0,0]), sigma_below))

    return sigma_new



        